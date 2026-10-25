"""Управляющий приносит бумагу, бухгалтер её разбирает (T174, D047).

Главное, что здесь проверяется, — **одно утверждение о деньгах**: принесённая
бумага в P&L не входит, пока её не разобрали. Не «помечена как неподтверждённая»,
а именно не входит: в отчёте живут строки учёта, а у бумаги их ноль. Это ровно то
свойство, которое отличает сбор первички от прямой записи в P&L человеком,
который не знает ни статьи, ни периода.

Рядом — правила, которые обязаны работать здесь так же, как на соседних экранах:
повторная отправка формы не заводит вторую бумагу, чужая точка отвергается базой
(D014) и отвечает неотличимо от несуществующей (D023), а отказ не оставляет
бумагу «наполовину принятой».

Проверки идут **через экраны**, то есть тем же путём, что человек. Прямое чтение
базы — только осмотр результата, и идёт оно владельцем схемы: это не проверка
доступа. Доступ проверяется отдельно и ролью `app_user`
(`tests/test_paper_access.py`).
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from conftest import body, login_as
from test_directory import payruns_restored, sql  # noqa: F401
from test_supplier_invoices import counterparty, item, tenant, units  # noqa: F401

PAPERS = "/papers/"
NEW = "/papers/new/"
INBOX = "/inbox/"
JULY_DAY = "2026-07-03"

# Настоящая подпись JPEG: продукт определяет тип по байтам, а не по имени файла
# и не по слову браузера, и проверка обязана идти тем же путём.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"snapshot-of-a-delivery-note"


def shot(name: str = "note.jpg", data: bytes = JPEG, content_type: str = "image/jpeg"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def key() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def papers_removed(sql, tenant):  # noqa: F811
    """Бумаги теста не переживают его.

    Просить эту фикстуру нужно **раньше** `payruns_restored`: разбираются они в
    обратном порядке, а строку закрытого месяца база удалить не даст
    (`facts_guard`), пока месяц не открыт заново.
    """
    before = [
        row[0] for row in sql.execute(
            "select period from periods where tenant_id = %s", (tenant,)
        ).fetchall()
    ]
    yield
    sql.execute(
        "delete from facts where document_id in "
        "(select id from source_documents where external_id like 'paper:%%')"
    )
    sql.execute(
        "delete from document_files where document_id in "
        "(select id from source_documents where external_id like 'paper:%%')"
    )
    sql.execute("delete from source_documents where external_id like 'paper:%%'")
    sql.execute(
        """delete from periods p
            where p.tenant_id = %s
              and p.period <> all(%s::date[])
              and not exists (select 1 from facts f
                               where f.tenant_id = p.tenant_id and f.period = p.period)
              and not exists (select 1 from payruns r
                               where r.tenant_id = p.tenant_id and r.period = p.period)""",
        (tenant, before),
    )


def hand_over(client, units, *, kind="invoice", unit="NS1",  # noqa: F811
              amount="18600.00", note="Delivery note from the warehouse",
              entry_key=None, file=None, **extra):
    """Скинуть бумагу так, как это делает человек: форма и файл одним POST."""
    form = {
        "entry_key": entry_key or key(),
        "kind": kind,
        "date": JULY_DAY,
        "note": note,
        "scan": shot() if file is None else file,
        **extra,
    }
    if unit is not None:
        form["unit"] = units[unit]
    if amount is not None:
        form["amount"] = amount
    return client.post(NEW, form)


def card_of(response) -> str:
    """Адрес карточки, на которую увёл продукт после приёма бумаги."""
    assert response.status_code == 302, body(response)
    return response["Location"]


def sum_in_pnl(sql, document_id) -> Decimal:  # noqa: F811
    """Сколько денег этого документа лежит в P&L. Ноль — их там нет вовсе."""
    return sql.execute(
        "select coalesce(sum(amount), 0) from pnl_lines where document_id = %s",
        (str(document_id),),
    ).fetchone()[0]


def document_id_of(sql, external_like="paper:%"):  # noqa: F811
    rows = sql.execute(
        "select id from source_documents where external_id like %s", (external_like,)
    ).fetchall()
    assert len(rows) == 1, f"документов не один, а {len(rows)}"
    return rows[0][0]


# --- бумага приходит с точки --------------------------------------------------


def test_the_manager_hands_over_a_delivery_note(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Управляющий скидывает накладную своей точки — и она принята."""
    login_as(client, "manager")
    landed = card_of(hand_over(client, units))

    card = body(client.get(landed))
    assert 'data-waiting="1"' in card, card
    assert "Накладная" in card
    listed = body(client.get(PAPERS))
    assert 'data-waiting="1"' in listed
    assert "NS1" in listed


def test_the_photograph_comes_back_byte_for_byte(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Файл отдаётся тем же, каким пришёл: разбирают именно его, а не миниатюру."""
    login_as(client, "manager")
    card_of(hand_over(client, units))
    document_id = document_id_of(sql)

    answer = client.get(f"/papers/{document_id}/file/")
    assert answer.status_code == 200
    assert answer["Content-Type"] == "image/jpeg"
    assert answer["X-Content-Type-Options"] == "nosniff"
    assert answer.content == JPEG


def test_a_paper_without_a_stated_amount_shows_a_dash_not_a_zero(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Сумму управляющий может не знать. Прочерк и ноль — разные вещи.

    Ноль в этой колонке читался бы как «бумага на нулевую сумму», а прочерк — как
    «сумма неизвестна», и это первое правило дизайн-системы о числах.
    """
    login_as(client, "manager")
    card_of(hand_over(client, units, amount=None))

    listed = body(client.get(PAPERS))
    assert "num num--empty" in listed, listed


def test_the_same_form_twice_hands_over_one_paper(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Второе нажатие на телефоне с плохой связью — та же бумага, не вторая."""
    login_as(client, "manager")
    once = key()
    hand_over(client, units, entry_key=once)
    hand_over(client, units, entry_key=once)

    assert sql.execute(
        "select count(*) from source_documents where external_id like 'paper:%%'"
    ).fetchone()[0] == 1
    assert sql.execute("select count(*) from document_files").fetchone()[0] == 1


# --- бумаги нет в P&L, пока её не разобрали ------------------------------------


def test_a_handed_paper_is_not_in_the_pnl_at_all(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Главное утверждение задачи: до разбора денег бумаги в отчёте нет.

    Сломайте это — и управляющий начнёт вносить расходы в P&L, называя суммы со
    слов: без статьи, без периода учёта и без разбора бухгалтером.
    """
    login_as(client, "manager")
    card_of(hand_over(client, units, amount="18600.00"))
    document_id = document_id_of(sql)

    assert sum_in_pnl(sql, document_id) == 0
    # И сумма при этом не потеряна: она видна человеку на экране.
    assert "18 600,00" in body(client.get(PAPERS))


def test_a_handed_paper_is_named_in_the_inbox_and_listed_on_its_own_page(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Инбокс называет бумагу числом и ведёт к ней, но сам её не показывает.

    Бумаги — на `/papers/` (T232, D081: одна страница — одна функция). Строки
    без статьи **уже** в P&L, только не в той статье, а бумаг в P&L нет вовсе:
    два списка на одном экране читаются как одна очередь с одной суммой.
    """
    login_as(client, "manager")
    card_of(hand_over(client, units, amount="18600.00"))
    document_id = document_id_of(sql)

    login_as(client, "accountant")
    inbox = body(client.get(INBOX))
    assert 'data-papers="1"' in inbox, inbox
    assert f'href="{PAPERS}"' in inbox, "из инбокса не попасть к бумагам"
    assert 'data-paper="' not in inbox, "бумаги по-прежнему списком в инбоксе"
    # Навигация — левой панелью (D081): «назад к счетам» у страницы верхнего
    # уровня дублирует её и ведёт «вверх» туда, где инбокс не лежит.
    assert "← К счетам" not in inbox
    assert f"/papers/{document_id}/" not in inbox
    assert "18 600" not in inbox, "сумма бумаги стоит в инбоксе рядом с суммами P&L"

    papers_page = body(client.get(PAPERS))
    assert 'data-waiting="1"' in papers_page and 'data-stated="18600.00"' in papers_page


# --- разбор -------------------------------------------------------------------


def review(client, *, counterparty, item, units,  # noqa: F811
           unit="NS1", amount="18600.00", document_id, **extra):
    """Разобрать бумагу с её же карточки: поля те же, что у счёта."""
    return client.post(f"/papers/{document_id}/", {
        "entry_key": key(),
        "date": JULY_DAY,
        "period": "2026-07",
        "counterparty": counterparty,
        "item": item,
        "unit": units[unit],
        "ledger": "official",
        "amount": amount,
        **extra,
    })


def test_the_accountant_sorts_the_paper_out_and_the_money_appears(
    client, units, counterparty, item, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Разбор бухгалтером: строка появляется в P&L, документ остаётся ТОТ ЖЕ.

    Второй документ был бы худшим из исходов: бумага осталась бы стоять в
    инбоксе с фотографией, а деньги уехали бы в документ-двойник, который никто
    не открывал.
    """
    login_as(client, "manager")
    card_of(hand_over(client, units))
    document_id = document_id_of(sql)

    login_as(client, "accountant")
    answer = review(client, counterparty=counterparty, item=item, units=units,
                    document_id=document_id)
    assert answer.status_code == 302, body(answer)

    assert document_id_of(sql) == document_id
    assert sum_in_pnl(sql, document_id) == Decimal("18600.00")

    card = body(client.get(f"/papers/{document_id}/"))
    assert 'data-waiting="0"' in card, card
    assert f"/invoices/{document_id}/" in card


def waiting_by_the_inbox(client) -> int:
    """Сколько бумаг ждёт — по ссылке инбокса. Нет ссылки — ноль."""
    found = re.search(r'data-papers="(\d+)"', body(client.get(INBOX)))
    return int(found.group(1)) if found else 0


def waiting_by_the_list(client) -> int:
    """Сколько бумаг ждёт — по числу сверху списка бумаг, а не по строкам."""
    found = re.search(r'<span data-waiting="(\d+)"', body(client.get(PAPERS)))
    assert found, "у списка бумаг нет числа ждущих"
    return int(found.group(1))


def test_a_not_ours_paper_waits_nowhere(
    client, units, counterparty, item, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Бумага «не наша» разобрана: строки сторнированы, но строки учёта есть.

    Ждущей её не считают ни инбокс, ни список бумаг — иначе она вернулась бы в
    очередь, из которой её только что убрали словами.
    """
    login_as(client, "manager")
    card_of(hand_over(client, units))
    document_id = document_id_of(sql)

    login_as(client, "accountant")
    review(client, counterparty=counterparty, item=item, units=units,
           document_id=document_id)
    answer = client.post(f"/invoices/{document_id}/not-ours/", {"why": "Соседний арендатор"})
    assert answer.status_code == 302, body(answer)[:300]

    assert waiting_by_the_inbox(client) == waiting_by_the_list(client) == 0


def test_the_inbox_and_the_list_count_waiting_papers_alike(
    client, units, counterparty, item, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Бумага, у которой не осталось ни одной действующей строки, ждёт — везде.

    Раньше ссылка инбокса считала бумагу разобранной по ЛЮБОЙ строке, включая
    заменённую, а список бумаг — только по действующей, и два экрана называли
    разные числа об одной очереди (issue #287). Строка здесь снимается прямо в
    базе: в продукте такого пути сейчас нет, но условие одно, и держит его тест.
    """
    login_as(client, "manager")
    card_of(hand_over(client, units))
    document_id = document_id_of(sql)

    login_as(client, "accountant")
    review(client, counterparty=counterparty, item=item, units=units,
           document_id=document_id)
    assert waiting_by_the_inbox(client) == waiting_by_the_list(client) == 0

    sql.execute("update facts set superseded_at = now() where document_id = %s",
                (document_id,))
    assert sql.execute(
        "select count(*) from facts where document_id = %s and superseded_at is null",
        (document_id,),
    ).fetchone()[0] == 0, "предохранитель: действующие строки остались"

    assert waiting_by_the_list(client) == 1
    assert waiting_by_the_inbox(client) == 1, "инбокс и список бумаг считают по-разному"


def test_month_closing_counts_waiting_papers_like_the_list(
    client, units, counterparty, item, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Готовность месяца к закрытию считает ждущие бумаги тем же условием.

    Своё условие закрытия («у бумаги нет ни одного факта») считало бумагу без
    единой действующей строки разобранной — и месяц закрывался с бумагой, денег
    которой в отчёте нет, пока список бумаг называл её ждущей.
    """
    from payrun.readiness import check

    login_as(client, "manager")
    card_of(hand_over(client, units))
    document_id = document_id_of(sql)
    login_as(client, "accountant")
    review(client, counterparty=counterparty, item=item, units=units,
           document_id=document_id)
    space, period = sql.execute(
        "select tenant_id, period from facts where document_id = %s limit 1", (document_id,)
    ).fetchone()
    sql.execute("update facts set superseded_at = now() where document_id = %s",
                (document_id,))

    assert waiting_by_the_list(client) == 1, "предохранитель: список бумаг её не ждёт"
    codes = [finding.code for finding in check(space, period).findings]
    assert "papers" in codes, f"закрытие не видит ждущую бумагу: {codes}"


def test_a_sorted_out_paper_leaves_the_inbox(
    client, units, counterparty, item, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Разобранная бумага уходит из очереди: иначе очередь перестают читать."""
    login_as(client, "manager")
    card_of(hand_over(client, units))
    document_id = document_id_of(sql)

    login_as(client, "accountant")
    review(client, counterparty=counterparty, item=item, units=units,
           document_id=document_id)

    inbox = body(client.get(INBOX))
    # Ждущих бумаг нет — нет и ссылки: «ждут разбора 0» ничего не сообщает.
    assert 'data-papers="' not in inbox, inbox


def test_a_receipt_stays_a_receipt_after_the_review(
    client, units, counterparty, item, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Чек остаётся чеком: разбор назначает статью, а не переписывает бумагу.

    Записать его счётом значило бы стереть то, что человек про бумагу знал, — и
    получить «счёт», которого поставщик никогда не выставлял.
    """
    login_as(client, "manager")
    card_of(hand_over(client, units, kind="receipt"))
    document_id = document_id_of(sql)

    login_as(client, "accountant")
    review(client, counterparty=counterparty, item=item, units=units,
           document_id=document_id)

    assert sql.execute(
        "select kind::text from source_documents where id = %s", (document_id,)
    ).fetchone()[0] == "receipt"
    # И карточка разобранного чека открывается, а не отвечает 404: иначе в
    # списке счетов осталась бы строка, ведущая в никуда.
    assert client.get(f"/invoices/{document_id}/").status_code == 200


def test_a_refused_review_leaves_the_paper_in_the_inbox(
    client, units, item, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Отказ на разборе не оставляет бумагу «наполовину разобранной».

    Документ без строк выглядел бы разобранным — молчаливый сбой, который в
    проекте-предшественнике стоил дорого. Здесь бумага обязана остаться в
    очереди и сказать об этом словами.
    """
    login_as(client, "manager")
    card_of(hand_over(client, units))
    document_id = document_id_of(sql)

    login_as(client, "accountant")
    answer = client.post(f"/papers/{document_id}/", {
        "entry_key": key(), "date": JULY_DAY, "item": item,
        "unit": units["NS1"], "ledger": "official", "amount": "18600.00",
        # Контрагента нет: без него счёт не на кого выписать.
    })
    assert answer.status_code in (400, 409), answer.status_code
    assert sum_in_pnl(sql, document_id) == 0
    assert 'data-waiting="1"' in body(client.get(f"/papers/{document_id}/"))
    assert 'data-papers="1"' in body(client.get(INBOX))


# --- чужое и негодное ---------------------------------------------------------


def test_the_manager_cannot_hand_over_a_paper_for_another_unit(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Подмена точки в форме отвергается базой, и бумага не заводится."""
    login_as(client, "manager")
    answer = hand_over(client, units, unit="BG1")

    assert answer.status_code in (400, 409, 403), answer.status_code
    assert sql.execute(
        "select count(*) from source_documents where external_id like 'paper:%%'"
    ).fetchone()[0] == 0


def test_a_stranger_paper_is_indistinguishable_from_a_made_up_one(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Бумага чужой точки и выдуманный номер отвечают одинаково — 404 (D023)."""
    login_as(client, "accountant")
    card_of(hand_over(client, units, unit="BG1"))
    document_id = document_id_of(sql)

    login_as(client, "manager")
    assert client.get(f"/papers/{document_id}/").status_code == 404
    assert client.get(f"/papers/{document_id}/file/").status_code == 404
    assert client.get(f"/papers/{uuid.uuid4()}/").status_code == 404


def test_a_file_that_is_not_a_photograph_is_refused_in_words(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Тип определяется по байтам: переименованный в .jpg текст не проходит."""
    login_as(client, "manager")
    answer = hand_over(
        client, units,
        file=shot("note.jpg", b"just some text, not a photograph", "image/jpeg"),
    )

    assert answer.status_code in (400, 409), answer.status_code
    assert "PDF" in body(answer)
    assert sql.execute("select count(*) from document_files").fetchone()[0] == 0


def test_a_paper_without_a_file_is_refused(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Без снимка разбирать нечего, и такая бумага в очередь не встаёт."""
    login_as(client, "manager")
    answer = client.post(NEW, {
        "entry_key": key(), "kind": "invoice", "date": JULY_DAY,
        "unit": units["NS1"], "amount": "18600.00",
    })

    assert answer.status_code in (400, 409), answer.status_code
    assert sql.execute(
        "select count(*) from source_documents where external_id like 'paper:%%'"
    ).fetchone()[0] == 0


def test_nobody_gets_a_paper_without_a_session(client, papers_removed):  # noqa: F811
    """Без входа не отдаётся ни список, ни файл: это данные партнёра."""
    assert client.get(PAPERS).status_code in (302, 403)
    assert client.get(NEW).status_code in (302, 403)



# --- кто вправе разбирать ------------------------------------------------------
#
# Обещание задачи — «в P&L не входит, пока БУХГАЛТЕР не разобрал». До этих
# проверок оно не выполнялось, и нарушалось не хитрым запросом: карточка бумаги
# печатала управляющему форму «Разобрать и учесть», нажатие ставило сумму в
# отчёт немедленно. Ни одной проверки на пути не было — кто пишет факт, решала
# только видимость строки политиками базы, а она знает про точку и регистр, но
# не про то, кому положено классифицировать (issue #143).


def test_the_one_who_brought_the_paper_is_not_offered_to_sort_it(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Управляющему форма разбора не показывается — и сказано, чего он ждёт.

    Проверяется и то, чего нет, и то, что есть вместо: исчезнувшая без
    объяснения форма читается как поломка продукта, а не как порядок.
    """
    login_as(client, "manager")
    card_of(hand_over(client, units))
    document_id = document_id_of(sql)

    card = body(client.get(f"/papers/{document_id}/"))
    assert "Разобрать и учесть" not in card, "управляющему предложили разобрать"
    assert "Разбирает бумагу тот, кто ведёт месяц" in card, card
    # Фотография и слова управляющего остаются: он вправе видеть, что донёс.
    assert 'data-waiting="1"' in card, card


def test_the_manager_cannot_sort_the_paper_even_by_posting_the_form(
    client, units, counterparty, item, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Форму убрали — но проверяется запрет, а не отсутствие кнопки.

    Кнопки нет в разметке, и на этом легко остановиться. Остановиться нельзя:
    POST отправляется и без кнопки, а деньги пишет он, а не она.
    """
    login_as(client, "manager")
    card_of(hand_over(client, units))
    document_id = document_id_of(sql)

    answer = review(client, counterparty=counterparty, item=item, units=units,
                    document_id=document_id)
    assert answer.status_code == 403, body(answer)
    assert sum_in_pnl(sql, document_id) == Decimal("0.00"), "деньги всё-таки записались"

    # Бумага цела и по-прежнему ждёт: отказ не должен ничего испортить.
    assert 'data-waiting="1"' in body(client.get(f"/papers/{document_id}/"))


def test_the_inbox_endpoint_is_closed_to_the_manager_too(
    client, units, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Закрыть одну поверхность мало: разбор ведут три, и путь у них общий.

    `/inbox/<id>/classify/` ходит в тот же `record_invoice`, что и карточка
    бумаги. Починка только на карточке оставила бы дыру, до которой доходят
    ровно те же двумя щелчками — через экран инбокса.
    """
    login_as(client, "manager")
    answer = client.post(f"/api/inbox/{uuid.uuid4()}/classify/", {})

    # Именно 403, а не 404, и это не придирка: право спрашивается ДО поиска
    # строки. Ответь эндпоинт «не найдено», и запрет держался бы на том, что
    # управляющий не угадал номер, — то есть не держался бы вовсе.
    assert answer.status_code == 403, body(answer)
    assert "Разбор первички" in body(answer), body(answer)

    # А тот, кому положено, до поиска строки доходит: 404 значит, что проверка
    # права его пропустила. Без этой половины проверка была бы неотличима от
    # эндпоинта, который всем отвечает отказом.
    login_as(client, "accountant")
    passed = client.post(f"/api/inbox/{uuid.uuid4()}/classify/", {})
    assert passed.status_code == 404, body(passed)


def test_the_manager_still_records_a_cash_expense_of_his_unit(
    client, units, item, papers_removed, payruns_restored, sql,  # noqa: F811
):
    """Право на разбор не отняло у управляющего его собственную работу.

    Ошибка здесь дороже той, от которой закрывались: T109 прямо требует, чтобы
    управляющий вносил наличный расход своей точки. Слишком широкое право
    отобрало бы у него это молча — и обнаружилось бы не тестом, а человеком,
    который не смог внести трату.
    """
    login_as(client, "manager")
    note = "мешки для мусора, проверка права"
    try:
        answer = client.post("/expenses/new/", {
            "date": JULY_DAY,
            "amount": "1200.00",
            "item": str(item),
            "note": note,
        })
        assert answer.status_code in (302, 200), body(answer)
        assert "не входит в права вашей роли" not in body(answer)
    finally:
        # Убрать за собой обязательно: расход остаётся в общей базе и ломает
        # соседние проверки, которые сверяют итоги периода со своими строками.
        # Поймано прогоном — в одиночку тест зелёный, в наборе валил четыре
        # чужих.
        sql.execute("delete from facts where note = %s", [note])
