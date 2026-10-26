"""Разбор первички в две руки (T206, issue #174, модуль эталона 15).

Эталон: «сначала операционный директор говорит, чья и за что, потом бухгалтер
сверяет сумму и НДС», и «в раскрытии видно накладную, кто разнёс и кто
подтвердил». Кто какой шаг делает — право в форме роли (`suppliers.classify`
и `suppliers.confirm`), а не имя роли в коде (D060).

Что здесь ядро, а что нет. Доступ и след — ядро: строка P&L, у которой
«подтвердил» стоит не тот, кто подтверждал, читается партнёром как факт.
Поэтому права проверяются ролью `app_user` прямо в базе, а не только экраном:
владелец схемы обходит RLS, и неправильная политика дала бы зелёный прогон.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import psycopg
import pytest

from conftest import as_app_user, body, login_as, narrowed_permissions
from test_supplier_invoices import (  # noqa: F401
    NEW,
    counterparty,
    invoice_form,
    invoices_removed,
    item,
    key,
    sql,
    tenant,
    units,
)

INBOX = "/inbox/"
CHECK = "/inbox/check/"


def person(sql, code):  # noqa: F811
    """Учётка сида разработки по коду роли: логин сида — код роли."""
    return str(sql.execute("select id from users where username = %s", (code,)).fetchone()[0])


def unsorted_line(client, sql, counterparty, units, number):  # noqa: F811
    """Счёт без статьи на точку BG1 — строка инбокса, ждущая разнесения."""
    login_as(client, "accountant")
    answer = client.post(NEW, invoice_form(counterparty, units, item="", number=number,
                                           entry_key=key()))
    assert answer.status_code == 302, body(answer)[:300]
    return active_line(sql, number)


def active_line(sql, number):  # noqa: F811
    return str(sql.execute(
        """select f.id from facts f join source_documents d on d.id = f.document_id
            where d.doc_number = %s and f.superseded_at is null""",
        (number,),
    ).fetchone()[0])


def reviews(sql, number):  # noqa: F811
    """След строки: (шаг, кто) по порядку — всё, что база о ней знает."""
    return sql.execute(
        """select r.step, r.done_by::text
             from fact_reviews r
             join facts f on f.id = r.fact_id
             join source_documents d on d.id = f.document_id
            where d.doc_number = %s
            order by r.done_at, r.step""",
        (number,),
    ).fetchall()


@pytest.fixture
def sorted_line(client, sql, item, counterparty, units, invoices_removed):  # noqa: F811
    """Строка, которую директор разнёс в инбоксе: точка и статья проставлены."""
    fact_id = unsorted_line(client, sql, counterparty, units, "TWO-1")
    login_as(client, "director")
    answer = client.post(f"/inbox/{fact_id}/classify/",
                         {"item": item, "unit": units["BG1"]})
    assert answer.status_code == 302 and "failed" not in answer["Location"], answer
    return active_line(sql, "TWO-1")


@contextmanager
def app_conn(dsn, user_id):
    """Соединение ролью приложения: ровно то, что видит и может сервис."""
    with psycopg.connect(dsn) as conn:
        with as_app_user(conn, user_id):
            yield conn
        conn.rollback()


def attempt(conn, statement, args):
    """Запрос в точке сохранения: отказ базы не обрывает соединение теста.

    Без неё отказ оставлял транзакцию сломанной, и `as_app_user` не мог даже
    вернуть роль — проверка падала не тем, что проверяла.
    """
    with conn.transaction():
        return conn.execute(statement, args).rowcount


def insert_review(conn, tenant, fact_id, step):  # noqa: F811
    """Записать шаг ролью приложения. Ноль строк — не отказ, а невидимая строка.

    Вставка идёт через `select` из `facts`, и строку, которую роль не видит,
    она молча пропустит. Такая «проверка» зеленела бы отказом, которого не было.
    """
    written = attempt(
        conn,
        """insert into fact_reviews (tenant_id, fact_id, fact_key, step)
           select tenant_id, id, dedup_key, %s from facts where id = %s""",
        (step, fact_id),
    )
    assert written == 1, "строка роли не видна — отказ не доказан"
    return conn.execute(
        "select count(*) from fact_reviews where fact_id = %s and step = %s",
        (fact_id, step),
    ).fetchone()[0]


# --- разбор пишет первую руку --------------------------------------------------


def test_sorting_out_records_who_allocated(sql, sorted_line):  # noqa: F811
    """Разбор в инбоксе оставляет след: шаг «разнесено» и имя разобравшего."""
    assert reviews(sql, "TWO-1") == [("allocated", person(sql, "director"))]


def test_the_check_queue_names_whose_decision_the_line_waits_for(
    client, sorted_line,  # noqa: F811
):
    """Разнесённая строка стоит в очереди сверки и говорит, кто её сверяет.

    Кто — роли, у которых есть право шага, из формы ролей: не «бухгалтер»
    строкой в шаблоне (D060).
    """
    login_as(client, "accountant")
    page = body(client.get(CHECK))
    assert sorted_line in page, "разнесённой строки нет в очереди сверки"
    assert "Ждёт сверки" in page
    assert "Бухгалтер" in page, "очередь не называет, чьего решения ждёт строка"


def test_the_inbox_says_the_line_waits_for_allocation(
    client, sql, counterparty, units, invoices_removed,  # noqa: F811
):
    """Строка без статьи в инбоксе подписана: ждёт разнесения — и кем."""
    fact_id = unsorted_line(client, sql, counterparty, units, "TWO-2")
    page = body(client.get(INBOX))
    assert fact_id in page
    assert "Ждёт разнесения" in page
    assert "Оперативный директор" in page


# --- вторая рука ---------------------------------------------------------------


def test_the_accountant_confirms_and_the_line_leaves_the_queue(
    client, sql, sorted_line,  # noqa: F811
):
    login_as(client, "accountant")
    answer = client.post(f"{CHECK}{sorted_line}/confirm/")
    assert answer.status_code == 302, body(answer)[:300]
    assert reviews(sql, "TWO-1") == [
        ("allocated", person(sql, "director")), ("confirmed", person(sql, "accountant")),
    ]
    assert sorted_line not in body(client.get(CHECK)), "сверенная строка осталась в очереди"


def test_whoever_allocated_cannot_confirm_on_the_screen(
    client, sql, sorted_line,  # noqa: F811
):
    """Две руки, а не одна дважды: разнёсший сверить свою же строку не может.

    Отказ — словами, а не 500 и не молчаливый редирект.
    """
    login_as(client, "director")
    answer = client.post(f"{CHECK}{sorted_line}/confirm/")
    assert answer.status_code == 403, answer.status_code
    assert "разнесли вы" in body(answer)
    assert reviews(sql, "TWO-1") == [("allocated", person(sql, "director"))]


def test_the_pnl_drilldown_shows_both_names(client, sql, tenant, sorted_line):  # noqa: F811
    login_as(client, "accountant")
    client.post(f"{CHECK}{sorted_line}/confirm/")
    period_id = sql.execute(
        "select p.id from periods p join facts f on f.period = p.period"
        " and f.tenant_id = p.tenant_id where f.id = %s", (sorted_line,),
    ).fetchone()[0]
    page = body(client.get(f"/periods/{period_id}/pnl/food_cost/"))
    row = page[page.index("EPS Elektro"):]
    row = row[:row.index("</tr>")]
    assert "Оперативный директор" in row, "в раскрытии нет того, кто разнёс"
    assert "Бухгалтер" in row, "в раскрытии нет того, кто подтвердил"


# --- ядро: права ролью app_user ------------------------------------------------


def test_the_allocation_step_needs_the_classify_right(
    web_env, tenant, sql, client, counterparty, units, invoices_removed,  # noqa: F811
):
    """Без `suppliers.classify` шаг «разнесено» не пишется — даже директором.

    Роль сужается, как это сделал бы партнёр экраном ролей: строка ей видна,
    и отказ приходит по праву, а не по видимости.
    """
    fact_id = unsorted_line(client, sql, counterparty, units, "TWO-3")
    rights = ["payroll.view", "suppliers.confirm"]
    with narrowed_permissions(web_env, "director", rights), \
            app_conn(web_env, person(sql, "director")) as conn, \
            pytest.raises(psycopg.Error):
        insert_review(conn, tenant, fact_id, "allocated")


def test_confirming_needs_the_confirm_right(web_env, sql, tenant, sorted_line):  # noqa: F811
    """Без `suppliers.confirm` сверка не записывается — даже бухгалтером."""
    rights = ["suppliers.classify", "payroll.view"]
    with narrowed_permissions(web_env, "accountant", rights), \
            app_conn(web_env, person(sql, "accountant")) as conn, pytest.raises(psycopg.Error):
        insert_review(conn, tenant, sorted_line, "confirmed")


def test_the_one_who_allocated_cannot_confirm_in_the_database(
    web_env, sql, tenant, sorted_line,  # noqa: F811
):
    """Правило двух рук стоит в политике, а не только на экране."""
    with app_conn(web_env, person(sql, "director")) as conn, pytest.raises(psycopg.Error):
        insert_review(conn, tenant, sorted_line, "confirmed")


def test_the_author_of_the_version_cannot_confirm_it(
    web_env, sql, tenant, sorted_line,  # noqa: F811
):
    """Новая версия строки мимо разбора (правка счёта) шага «разнесено» не
    оставляет — и её автор не должен сверять собственную работу.

    Версия «написана бухгалтером» правкой автора владельцем схемы: так база
    выглядит после того, как бухгалтер переписал разнесённую строку сам.
    """
    sql.execute("update facts set created_by = %s where id = %s",
                (person(sql, "accountant"), sorted_line))
    with app_conn(web_env, person(sql, "accountant")) as conn, \
            pytest.raises(psycopg.Error):
        insert_review(conn, tenant, sorted_line, "confirmed")


def test_the_accountant_may_confirm_in_the_database(web_env, sql, tenant, sorted_line):  # noqa: F811
    """Положительная половина: иначе отказы выше доказывали бы закрытую дверь."""
    with app_conn(web_env, person(sql, "accountant")) as conn:
        assert insert_review(conn, tenant, sorted_line, "confirmed") == 1


def test_nothing_unallocated_can_be_confirmed(
    web_env, tenant, sql, client, counterparty, units, invoices_removed,  # noqa: F811
):
    """Сверять нечего, пока никто не сказал, чья строка и за что."""
    fact_id = unsorted_line(client, sql, counterparty, units, "TWO-4")
    with app_conn(web_env, person(sql, "accountant")) as conn, pytest.raises(psycopg.Error):
        insert_review(conn, tenant, fact_id, "confirmed")


def test_a_replaced_version_cannot_be_confirmed(
    web_env, tenant, sql, client, item, counterparty, units, invoices_removed,  # noqa: F811
):
    """Сверяется действующая версия: сверка старой ничего не говорит о новой."""
    old = unsorted_line(client, sql, counterparty, units, "TWO-5")
    login_as(client, "director")
    client.post(f"/inbox/{old}/classify/", {"item": item, "unit": units["BG1"]})
    assert active_line(sql, "TWO-5") != old, "предохранитель: версия не сменилась"
    with app_conn(web_env, person(sql, "accountant")) as conn, pytest.raises(psycopg.Error):
        insert_review(conn, tenant, old, "confirmed")


def test_the_author_cannot_be_forged(web_env, sql, tenant, sorted_line):  # noqa: F811
    """Имя в следе ставит база: вписать чужое нельзя правом на колонку."""
    with app_conn(web_env, person(sql, "accountant")) as conn, pytest.raises(psycopg.Error):
        attempt(
            conn,
            """insert into fact_reviews (tenant_id, fact_id, fact_key, step, done_by)
               select tenant_id, id, dedup_key, 'confirmed', %s from facts where id = %s""",
            (str(uuid.uuid4()), sorted_line),
        )


@pytest.mark.parametrize("statement", [
    "update fact_reviews set done_by = %s where fact_id = %s",
    "delete from fact_reviews where fact_id = %s",
])
def test_the_trail_is_never_rewritten(web_env, sql, sorted_line, statement):  # noqa: F811
    """История не переписывается: запрет стоит правами таблицы, а не политикой."""
    args = (person(sql, "accountant"), sorted_line) if statement.startswith("update") \
        else (sorted_line,)
    with app_conn(web_env, person(sql, "director")) as conn, pytest.raises(psycopg.Error):
        attempt(conn, statement, args)


def test_a_stranger_sees_no_trail(web_env, sorted_line):  # noqa: F811
    """Учётка без членства у партнёра следа не видит (`tenant_isolation`)."""
    with app_conn(web_env, str(uuid.uuid4())) as conn:
        seen = conn.execute(
            "select count(*) from fact_reviews where fact_id = %s", (sorted_line,),
        ).fetchone()[0]
    assert seen == 0


def test_without_the_confirm_right_the_queue_explains_instead_of_crashing(
    client, sorted_line,  # noqa: F811
):
    """Нет права сверки — очередь видна, вверху сказано почему кнопки нет.

    Найдено браузером: состояние «нет права» роняло страницу пятисоткой.
    """
    login_as(client, "manager")
    answer = client.get(CHECK)
    assert answer.status_code == 200, answer.status_code
    assert "Сверено" not in body(answer)
