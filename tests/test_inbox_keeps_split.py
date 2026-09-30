"""Разбор строки в инбоксе не стирает её разнесение по точкам (issue #288, D089).

Сценарий найден зондом через веб-формы: счёт без статьи на «Вся сеть» разнесён
руками поровну на три точки (3 × 8000), строка стоит в инбоксе — статьи у неё
по-прежнему нет, — и «Разобрать» заменял родителя и все три доли одной строкой
на 24 000 на выбранной точке. Сумма не удваивалась, но работа человека пропадала
без единого слова.

Правило D089: разбор меняет только статью, доли остаются; точку при разборе
выбирают только у неразнесённой строки.

Здесь же «Разнести по точкам» из инбокса (эталон модуля 3): одиночно — ссылкой
на экран «Чья накладная», пачкой — одними долями, и только если точки у
отмеченных строк одни и те же (юрлицо одно).
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

INBOX = "/inbox/"


def network_invoice(client, sql, counterparty, units, number):  # noqa: F811
    """Счёт без статьи на всю сеть — строка инбокса, ждущая разнесения."""
    answer = client.post(NEW, invoice_form(counterparty, units, item="", unit="network",
                                           number=number, entry_key=key()))
    assert answer.status_code == 302, body(answer)[:300]
    return str(sql.execute(
        """select f.id from facts f join source_documents d on d.id = f.document_id
            where d.doc_number = %s and f.superseded_at is null""",
        (number,),
    ).fetchone()[0])


def shares_of(sql, number):  # noqa: F811
    """Действующие доли счёта: точка → (сумма, код статьи)."""
    return {
        row[0]: (row[1], row[2])
        for row in sql.execute(
            """select u.code, f.amount, e.code
                 from facts f
                 join source_documents d on d.id = f.document_id
                 join units u on u.id = f.unit_id
                 left join expense_items e on e.id = f.expense_item_id
                where d.doc_number = %s and f.superseded_at is null
                  and f.allocation = 'allocated'""",
            (number,),
        ).fetchall()
    }


def counted(sql, number):  # noqa: F811
    """Сколько счёта считается в P&L: действующие строки, кроме родителя-`split`."""
    return sql.execute(
        """select coalesce(sum(f.amount), 0)
             from facts f join source_documents d on d.id = f.document_id
            where d.doc_number = %s and f.superseded_at is null
              and f.allocation <> 'split'""",
        (number,),
    ).fetchone()[0]


def item_code(sql, item_id):  # noqa: F811
    return sql.execute("select code from expense_items where id = %s", (item_id,)).fetchone()[0]


@pytest.fixture
def split_row(client, sql, counterparty, units, invoices_removed):  # noqa: F811
    """Строка инбокса, разнесённая руками поровну на все точки юрлица."""
    login_as(client, "accountant")
    fact_id = network_invoice(client, sql, counterparty, units, "SPL-1")
    answer = client.post("/expenses/split/", {"fact": fact_id, "evenly": "1"})
    assert answer.status_code == 302, body(answer)[:300]
    before = shares_of(sql, "SPL-1")
    assert len(before) >= 2, f"предохранитель: строка не разнеслась — {before}"
    return fact_id, before


# --- ядро: разбор не стирает доли ---------------------------------------------


def test_sorting_out_a_split_row_keeps_its_shares(client, sql, item, split_row):  # noqa: F811
    """Статья проставлена, доли — те же точки и те же суммы."""
    fact_id, before = split_row
    answer = client.post(f"/inbox/{fact_id}/classify/", {"item": item})
    assert answer.status_code == 302, body(answer)[:300]
    assert "failed" not in answer["Location"], answer["Location"]

    after = shares_of(sql, "SPL-1")
    code = item_code(sql, item)
    assert {unit: amount for unit, (amount, _c) in after.items()} == {
        unit: amount for unit, (amount, _c) in before.items()
    }, f"доли не сохранились: было {before}, стало {after}"
    assert all(c == code for _a, c in after.values()), f"у долей нет статьи: {after}"
    assert counted(sql, "SPL-1") == Decimal("24000.00")
    assert fact_id not in body(client.get(INBOX)), "разобранная строка осталась в инбоксе"


def test_a_unit_sent_with_the_sort_does_not_collapse_the_shares(
    client, sql, item, units, split_row,  # noqa: F811
):
    """Точка, пришедшая в форме, у разнесённой строки не применяется.

    Старая форма присылала точку всегда — и разбор складывал доли в одну.
    """
    fact_id, before = split_row
    client.post(f"/inbox/{fact_id}/classify/", {"item": item, "unit": units["BG1"]})
    assert set(shares_of(sql, "SPL-1")) == set(before), "доли свернулись в одну точку"
    assert counted(sql, "SPL-1") == Decimal("24000.00")


def test_a_batch_sort_keeps_the_shares_too(client, sql, item, units, split_row):  # noqa: F811
    """Пачкой — то же правило: статья всем, доли разнесённых на месте."""
    fact_id, before = split_row
    answer = client.post("/inbox/classify/", {"facts": [fact_id], "item": item,
                                              "unit": units["BG1"]})
    assert answer.status_code == 302, body(answer)[:300]
    after = shares_of(sql, "SPL-1")
    assert set(after) == set(before), f"доли свернулись: {after}"
    assert all(c == item_code(sql, item) for _a, c in after.values())


def test_the_inbox_offers_no_unit_choice_for_a_split_row(client, sql, split_row):  # noqa: F811
    """У разнесённой строки выбора точки нет: он бы ничего не сделал — или сделал вред."""
    fact_id, _before = split_row
    page = body(client.get(INBOX))
    assert f'id="{fact_id}-unit"' not in page, "у разнесённой строки предложена точка"
    assert f'id="{fact_id}-item"' in page, "у разнесённой строки нет выбора статьи"


# --- «Разнести по точкам» из инбокса ------------------------------------------


def test_a_network_row_leads_to_its_split_screen(
    client, sql, counterparty, units, invoices_removed,  # noqa: F811
):
    """Одиночно: строка на всю сеть ведёт на экран «Чья накладная»."""
    login_as(client, "accountant")
    fact_id = network_invoice(client, sql, counterparty, units, "SPL-2")
    page = body(client.get(INBOX))
    assert f'href="/expenses/{fact_id}/split/"' in page, "у строки на всю сеть нет разнесения"


def test_a_batch_is_split_with_one_set_of_shares(
    client, sql, counterparty, units, invoices_removed,  # noqa: F811
):
    """Пачкой: две строки одного юрлица — одни доли, обе разнесены."""
    login_as(client, "accountant")
    first = network_invoice(client, sql, counterparty, units, "SPL-3")
    second = network_invoice(client, sql, counterparty, units, "SPL-4")

    page = client.post("/inbox/split/", {"facts": [first, second]})
    assert page.status_code == 200, body(page)[:300]
    assert 'name="apply"' in body(page)

    answer = client.post("/inbox/split/", {"facts": [first, second], "evenly": "1",
                                           "apply": "1"})
    assert answer.status_code == 302, body(answer)[:400]
    assert len(shares_of(sql, "SPL-3")) >= 2 and len(shares_of(sql, "SPL-4")) >= 2
    assert counted(sql, "SPL-3") == counted(sql, "SPL-4") == Decimal("24000.00")


def test_a_batch_of_different_legal_entities_is_refused_in_words(
    request, client, sql, counterparty, units, invoices_removed,  # noqa: F811
):
    """Точки у строк разные — одни доли на них не лягут: отказ словами, ничего не записано."""
    login_as(client, "accountant")
    first = network_invoice(client, sql, counterparty, units, "SPL-5")
    second = network_invoice(client, sql, counterparty, units, "SPL-6")
    # Второе юрлицо заводится здесь: в сиде оно одно, а пачка разных юрлиц —
    # ровно тот случай, ради которого отказ написан.
    other = sql.execute(
        """insert into legal_entities (tenant_id, title)
           select tenant_id, 'Соседнее юрлицо T234' from facts where id = %s
           returning id""",
        (first,),
    ).fetchone()[0]
    sql.execute("update facts set legal_entity_id = %s where id = %s", (other, second))
    request.addfinalizer(lambda: (
        sql.execute("delete from facts where legal_entity_id = %s", (other,)),
        sql.execute("delete from legal_entities where id = %s", (other,)),
    ))

    answer = client.post("/inbox/split/", {"facts": [first, second], "evenly": "1",
                                           "apply": "1"})
    assert answer.status_code == 400, answer.status_code
    assert "юрлиц" in body(answer), body(answer)[:400]
    assert shares_of(sql, "SPL-5") == {} and shares_of(sql, "SPL-6") == {}


def test_a_row_already_on_a_unit_is_not_split_in_a_batch(
    client, sql, counterparty, units, invoices_removed,  # noqa: F811
):
    """Строка на точке разносить нечего — отказ словами, а не молчаливый пропуск."""
    login_as(client, "accountant")
    network = network_invoice(client, sql, counterparty, units, "SPL-7")
    client.post(NEW, invoice_form(counterparty, units, item="", number="SPL-8",
                                  entry_key=key()))
    on_unit = str(sql.execute(
        """select f.id from facts f join source_documents d on d.id = f.document_id
            where d.doc_number = 'SPL-8' and f.superseded_at is null"""
    ).fetchone()[0])
    answer = client.post("/inbox/split/", {"facts": [network, on_unit], "evenly": "1",
                                           "apply": "1"})
    assert answer.status_code == 400, answer.status_code
    assert shares_of(sql, "SPL-7") == {}


def test_nothing_selected_is_refused_in_words(client, sql, invoices_removed):  # noqa: F811
    login_as(client, "accountant")
    answer = client.post("/inbox/split/", {})
    assert answer.status_code == 400
    assert "Отметьте" in body(answer)


def test_the_manager_cannot_split_from_the_inbox(
    client, sql, counterparty, units, invoices_removed,  # noqa: F811
):
    """Строка на всю сеть управляющему не видна: 404, как чужая (D023)."""
    login_as(client, "accountant")
    fact_id = network_invoice(client, sql, counterparty, units, "SPL-9")
    login_as(client, "manager")
    answer = client.post("/inbox/split/", {"facts": [fact_id], "evenly": "1", "apply": "1"})
    assert answer.status_code in (403, 404), answer.status_code
    login_as(client, "accountant")
    assert shares_of(sql, "SPL-9") == {}


# --- разбор денег после ревизии (M1–M3, L1) -----------------------------------


def test_a_share_on_a_unit_that_is_not_offered_is_refused(
    client, sql, counterparty, units, invoices_removed,  # noqa: F811
):
    """Доля на точке, которой нет среди предложенных, — отказ словами.

    База молча выбрасывала такую долю и нормировала остальные: 60% на BG1 и 40%
    на выдуманный номер давали всю сумму на BG1 с ответом «разнесено».
    """
    import uuid

    login_as(client, "accountant")
    fact_id = network_invoice(client, sql, counterparty, units, "SPL-10")
    answer = client.post("/expenses/split/", {
        "fact": fact_id, f"share:{units['BG1']}": "60", f"share:{uuid.uuid4()}": "40",
    })
    assert answer.status_code == 400, answer.status_code
    assert "точк" in body(answer).lower()
    assert shares_of(sql, "SPL-10") == {}, "доля на чужой точке молча ушла на другие"


def test_the_batch_refuses_a_share_on_a_unit_that_is_not_offered(
    client, sql, counterparty, units, invoices_removed,  # noqa: F811
):
    """Пачка наследует ту же проверку: ничего не разнесено."""
    import uuid

    login_as(client, "accountant")
    fact_id = network_invoice(client, sql, counterparty, units, "SPL-11")
    answer = client.post("/inbox/split/", {
        "facts": [fact_id], "apply": "1",
        f"share:{units['BG1']}": "60", f"share:{uuid.uuid4()}": "40",
    })
    assert answer.status_code == 400, answer.status_code
    assert shares_of(sql, "SPL-11") == {}


def test_a_batch_line_that_writes_nothing_rolls_the_batch_back(
    client, sql, counterparty, units, invoices_removed, monkeypatch,  # noqa: F811
):
    """Ноль записанных строк — не успех: пачка откатывается целиком и говорит словами."""
    from web import cash as cash_module

    login_as(client, "accountant")
    first = network_invoice(client, sql, counterparty, units, "SPL-12")
    second = network_invoice(client, sql, counterparty, units, "SPL-13")
    real = cash_module.split_by_hand
    monkeypatch.setattr(cash_module, "split_by_hand",
                        lambda fact_id, shares, actor: 0 if str(fact_id) == second
                        else real(fact_id, shares, actor))

    answer = client.post("/inbox/split/", {"facts": [first, second], "evenly": "1",
                                           "apply": "1"})
    assert answer.status_code in (400, 409), answer.status_code
    assert shares_of(sql, "SPL-12") == {}, "пачка разнесена наполовину"


def test_a_split_credit_note_is_sorted_out_with_its_shares(
    client, sql, counterparty, units, item, invoices_removed,  # noqa: F811
):
    """Кредит-нота (отрицательная сумма), разнесённая по точкам, разбирается с долями.

    Веса долей отрицательные, а база берёт только положительные — разбор всегда
    получал отказ с ложной причиной «часть точек вам не видна».
    """
    login_as(client, "accountant")
    fact_id = network_invoice(client, sql, counterparty, units, "SPL-14")
    sql.execute("update facts set amount = -24000 where id = %s", (fact_id,))
    answer = client.post("/expenses/split/", {"fact": fact_id, "evenly": "1"})
    assert answer.status_code == 302, body(answer)[:300]
    before = shares_of(sql, "SPL-14")
    assert before and all(amount < 0 for amount, _c in before.values()), before

    answer = client.post(f"/inbox/{fact_id}/classify/", {"item": item})
    assert "failed" not in answer["Location"], "кредит-ноту разобрать не дали"
    after = shares_of(sql, "SPL-14")
    assert {u: a for u, (a, _c) in after.items()} == {u: a for u, (a, _c) in before.items()}
    assert counted(sql, "SPL-14") == Decimal("-24000.00")


def test_a_refused_sort_leaves_no_memory_of_the_supplier(
    client, sql, item, split_row, monkeypatch,  # noqa: F811
):
    """Разбор отказан — «поставщик → статья» не запоминается: строка не разобрана."""
    from web import cash as cash_module

    fact_id, _before = split_row
    sql.execute("delete from classification_rules")
    monkeypatch.setattr(cash_module, "split_by_hand", lambda *args: None)

    answer = client.post(f"/inbox/{fact_id}/classify/", {"item": item})
    assert "failed" in answer["Location"], "предохранитель: отказа не было"
    assert sql.execute("select count(*) from classification_rules").fetchone()[0] == 0, (
        "отказанный разбор оставил подсказку"
    )
