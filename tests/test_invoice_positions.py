"""Накладную можно разложить на позиции с разными статьями (issue #174, T204).

Модуль 3 эталона, вкладка «Разбор документа»: одна бумага раскрывается
позициями, у каждой своя статья и своя точка, а внизу видно, сходится ли сумма
позиций с суммой документа — «не сходится с позициями на 184 320». Пока не
сойдётся, проводить нельзя.

Зачем это нужно. Накладная из Метро — это еда и канцелярия в одной бумаге, и это
**разные строки P&L**. Пока документ можно отнести только к одной статье,
бухгалтер либо ставит одну на всё (и P&L врёт), либо заводит два счёта на одну
бумагу (и оплата разъезжается с документом).

Форма данных это уже позволяла: позиция и есть факт (`document_id` + `line_no`),
отдельной таблицы позиций в схеме нет намеренно (`0230_facts`). Не хватало двух
вещей — экрана и **сверки с суммой документа**: без неё разложить можно, а
заметить потерянную позицию нечем.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import body, login_as
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


@pytest.fixture
def invoice(client, sql, counterparty, units, invoices_removed):  # noqa: F811
    """Счёт на 24 000: одна позиция, как его вносят сегодня.

    Адрес карточки берётся из базы, а не из редиректа: после записи продукт
    уводит на СПИСОК счетов, и «адрес ответа плюс хвост» дал бы мусор, который
    Django разобрал бы как список — тест при этом выглядел бы работающим.
    """
    login_as(client, "accountant")
    answer = client.post(NEW, invoice_form(counterparty, units, number="POS-1"))
    assert answer.status_code == 302, body(answer)
    document = sql.execute(
        "select id from source_documents where doc_number = 'POS-1'"
    ).fetchone()[0]
    return f"/invoices/{document}/"


def positions(sql):  # noqa: F811
    return sql.execute(
        """select f.line_no, f.amount, e.code
             from facts f left join expense_items e on e.id = f.expense_item_id
            where f.dedup_key like 'manual:invoice:%%' and f.superseded_at is null
            order by f.line_no nulls first, f.created_at"""
    ).fetchall()


def add_position(client, url, **fields):
    """Добавить позицию так, как это делает форма разбора документа.

    Точка обязательна и у позиции — как у счёта: расход без точки не попадёт ни
    в один отчёт по точкам. «Вся сеть» тоже ответ, и она передаётся так же.
    """
    form = {"amount": "4000.00", "note": "Канцелярия", "unit": "network", **fields}
    return client.post(url + "positions/", form, follow=True)


# --- ядро ---------------------------------------------------------------------


def test_a_second_position_gets_its_own_article(client, sql, item, invoice):  # noqa: F811
    """Вторая позиция той же бумаги живёт со своей статьёй и своей суммой."""
    answer = add_position(client, invoice, item=item, amount="4000.00")
    assert answer.status_code == 200, body(answer)[:400]

    rows = positions(sql)
    assert len(rows) == 2, f"позиций не две: {rows}"
    assert sum(row[1] for row in rows) == Decimal("28000.00")


def test_the_card_says_the_positions_do_not_add_up(client, sql, item, invoice):  # noqa: F811
    """Сумма позиций разошлась с суммой документа — это сказано числом.

    Эталон: «не сходится с позициями на N». Без этой строки разложить можно, а
    заметить потерянную позицию нечем — расхождение всплывёт на сборке P&L.
    """
    add_position(client, invoice, item=item, amount="4000.00")

    # И на странице позиций, где их раскладывают, и на карточке счёта: там
    # позиций нет, но счёт с потерянной позицией не должен выглядеть целым.
    for shown in (body(client.get(invoice + "positions/")), body(client.get(invoice))):
        assert "не сходится" in shown.lower(), "расхождение не названо"
        assert "4 000" in shown, "не сказано, на сколько разошлось"
        assert 'data-difference="-4000.00"' in shown, "разница не та"


def test_the_card_says_it_adds_up_when_it_does(client, sql, item, invoice):  # noqa: F811
    """Сошлось — так и сказано: молчание читается как «не проверяли»."""
    for shown in (body(client.get(invoice + "positions/")), body(client.get(invoice))):
        assert "сходится" in shown.lower()
        assert "не сходится" not in shown.lower()


def test_a_position_without_an_amount_is_refused(client, sql, item, invoice):  # noqa: F811
    """Позиция без суммы — отказ словами, а не строка на ноль."""
    answer = add_position(client, invoice, item=item, amount="")
    assert answer.status_code == 400, body(answer)[:300]
    assert len(positions(sql)) == 1


def test_the_sum_of_positions_is_the_sum_of_the_invoice(client, sql, item, invoice):  # noqa: F811
    """Сумма счёта — это сложение позиций, а не отдельная колонка.

    Колонка была бы вторым ответом на тот же вопрос и разошлась бы с первым на
    первой же добавленной позиции — молча. Тот же довод, что у оплаты
    (`_summary`).
    """
    add_position(client, invoice, item=item, amount="4000.00")
    shown = body(client.get(invoice))
    assert "28 000" in shown, "сумма счёта не выросла на позицию"


def test_a_position_keeps_the_ledger_of_the_document(client, sql, item, invoice):  # noqa: F811
    """Позиция ложится в тот же регистр, что и счёт: бумага одна.

    Регистр у позиции не спрашивается вовсе. Половина бумаги в официальном, а
    половина в дополнительном — это не позиции одного документа, а два разных
    документа, и заводятся они отдельно.
    """
    add_position(client, invoice, item=item, amount="4000.00")
    ledgers = sql.execute(
        """select distinct ledger::text from facts
            where dedup_key like 'manual:invoice:%%' and superseded_at is null"""
    ).fetchall()
    assert ledgers == [("official",)], f"позиция ушла в другой регистр: {ledgers}"


def test_a_repeated_submit_does_not_double_the_position(client, sql, item, invoice):  # noqa: F811
    """Дважды отправленная форма не добавляет позицию дважды.

    Ключ позиции приезжает из формы (`entry_key`), поэтому повторная отправка
    той же формы заменяет ту же строку, а не плодит новые. Без этого двойной
    щелчок удваивал бы расход.
    """
    same = "position-key-1"
    add_position(client, invoice, item=item, amount="4000.00", entry_key=same)
    add_position(client, invoice, item=item, amount="4000.00", entry_key=same)

    rows = positions(sql)
    assert len(rows) == 2, f"повторная отправка удвоила позицию: {rows}"


def test_the_manager_cannot_add_a_position_to_a_stranger_invoice(client, sql, item, invoice):  # noqa: F811
    """Чужой счёт для управляющего точки не существует — и позиции ему не добавить."""
    login_as(client, "manager")
    answer = client.post(invoice + "positions/",
                         {"amount": "4000.00", "item": item, "unit": "network"})
    assert answer.status_code in (403, 404), body(answer)[:300]
    assert len(positions(sql)) == 1



# --- своя страница (T232, D081: одна страница — одна функция) ------------------


def test_the_card_leads_to_the_positions_instead_of_holding_them(client, sql, item, invoice):  # noqa: F811
    """Карточка правит счёт, позиции — на своей странице.

    На карточке нет формы добавления позиции: две формы записи денег на одном
    экране однажды перепутают. Есть ссылка с числом позиций — иначе о
    странице никто не узнает.
    """
    add_position(client, invoice, item=item, amount="4000.00")
    card = body(client.get(invoice))
    assert f'action="{invoice}positions/"' not in card, "форма позиции осталась на карточке"
    assert f'href="{invoice}positions/"' in card, "с карточки не попасть к позициям"
    assert f'<a class="btn" href="{invoice}positions/">Разнести по статьям</a>' in card, (
        "на карточке нет кнопки «Разнести по статьям» — ссылка в тексте не читается как действие"
    )
    assert 'data-positions="2"' in card

    page = client.get(invoice + "positions/")
    assert page.status_code == 200
    page = body(page)
    assert f'action="{invoice}positions/"' in page, "на странице позиций нет формы"
    assert page.count('data-amount="') == 2, "в таблице не две позиции"
    assert f'action="{invoice}positions/"' in page, "на странице позиций нет формы"
    assert page.count('data-amount="') == 2, "в таблице не две позиции"
    titles = sql.execute("select titles from expense_items where id = %s", (item,)).fetchone()[0]
    # Только внутри таблицы: та же статья стоит в выпадающем списке формы ниже,
    # и поиск по всей странице нашёл бы её там при пустой колонке.
    table = page[page.index("<tbody>"):page.index("</tbody>")]
    assert any(title and title in table for title in titles.values()), (
        "у позиции не видно статьи — а разложить бумагу по статьям и есть смысл страницы"
    )
    assert f'href="{invoice}"' in page, "со страницы позиций не вернуться к счёту"


def test_the_form_key_keeps_a_double_click_from_doubling(client, sql, item, invoice):  # noqa: F811
    """Ключ записи приезжает со страницы: двойное нажатие — одна позиция.

    Без скрытого поля каждая отправка получала новый ключ, и двойной щелчок по
    «Добавить позицию» удваивал расход.
    """
    import re

    page = body(client.get(invoice + "positions/"))
    found = re.search(r'name="entry_key" value="([^"]+)"', page)
    assert found, "в форме позиции нет ключа записи"
    for _ in range(2):
        add_position(client, invoice, item=item, amount="4000.00", entry_key=found.group(1))
    assert len(positions(sql)) == 2, f"двойная отправка удвоила позицию: {positions(sql)}"


def test_a_refused_position_is_said_on_its_own_page(client, sql, item, invoice):  # noqa: F811
    """Отказ — на странице позиций, со словами и с тем, что человек набрал."""
    answer = client.post(invoice + "positions/", {"amount": "4000.00", "item": item})
    assert answer.status_code == 400, body(answer)[:300]
    shown = body(answer)
    assert "Позиция не добавлена" in shown
    assert 'value="4000.00"' in shown, "набранная сумма потерялась на отказе"
    assert len(positions(sql)) == 1


def test_the_added_position_is_confirmed_in_words(client, sql, item, invoice):  # noqa: F811
    """После добавления сказано, в какой период легла позиция: молчание — не ответ."""
    answer = add_position(client, invoice, item=item, amount="4000.00")
    assert "Позиция добавлена и учтена в периоде" in body(answer)


def test_a_stranger_invoice_positions_are_not_found_for_the_manager(client, sql, item, invoice):  # noqa: F811
    """Чужой счёт управляющему не показывается и на странице позиций: 404.

    Счёт внесён на точку, которой у управляющего нет. Ответ тот же, что у
    несуществующего адреса, — по нему нельзя понять, что счёт есть (D023).
    """
    login_as(client, "manager")
    assert client.get(invoice).status_code == 404, "предохранитель: карточка видна"
    assert client.get(invoice + "positions/").status_code == 404
    answer = client.post(invoice + "positions/",
                         {"amount": "4000.00", "item": item, "unit": "network"})
    assert answer.status_code == 404
    assert len(positions(sql)) == 1


def test_without_a_partner_the_positions_refuse_in_words(client, sql, invoice):  # noqa: F811
    """Без партнёра — 403 и фраза, та же, что у карточки счёта, на GET и на POST."""
    from core.models import User

    person = User.objects.create_user(username="positions-nobody", password="secret-1")
    try:
        client.force_login(person)
        for answer in (client.get(invoice), client.get(invoice + "positions/"),
                       client.post(invoice + "positions/", {"amount": "1.00"})):
            assert answer.status_code == 403, answer.status_code
            assert "Вас ещё не завели ни к одному партнёру" in body(answer)
        assert len(positions(sql)) == 1
    finally:
        client.logout()
        person.delete()


def test_the_positions_table_names_the_article_of_each_line(client, sql, item, invoice):  # noqa: F811
    """У каждой позиции видна статья, и неразобранная названа неразобранной.

    Разложить бумагу по статьям — смысл страницы. Название позиции — снимок, и у
    строки без статьи это имя поставщика: по нему не видно, что статьи нет.
    """
    add_position(client, invoice, amount="4000.00")  # без статьи
    page = body(client.get(invoice + "positions/"))
    # Только внутри таблицы: та же фраза стоит пустым вариантом списка в форме ниже.
    table = page[page.index("<tbody>"):page.index("</tbody>")]
    assert "Пока не разобрано" in table, "у позиции без статьи не сказано, что статьи нет"
