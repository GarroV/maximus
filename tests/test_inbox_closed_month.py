"""Инбокс закрытого месяца показывает только то, что реально ждёт разбора (issue #290).

Разбор строки закрытого месяца не трогает её саму (`facts_guard`, D020): в
открытом периоде ложатся сторно исходной суммы и исправление со статьёй. Деньги
сходятся, но инбокс видел две строки без статьи — исходную и её сторно — и
показывал уже разобранную трату неразобранной, да ещё с отрицательной суммой
рядом. Человек разбирал её второй раз.

Правило одно (`suppliers.waiting_for_an_article`): ждёт разбора строка без
статьи, которую не сторнировали, и которая сама не сторно. По нему же отвечают
разбор по номеру (`unclassified_fact`), счётчик левой панели и готовность месяца.
"""
from __future__ import annotations

from decimal import Decimal

from conftest import body, login_as
from test_directory import approve_june, payruns_restored  # noqa: F401
from test_inbox_keeps_split import INBOX, network_invoice, shares_of
from test_supplier_invoices import (  # noqa: F401
    counterparty,
    invoices_removed,
    item,
    sql,
    tenant,
    units,
)


def inbox_rows_of(sql, number):  # noqa: F811
    """Строки счёта, которые инбокс показывает (по номерам фактов на экране)."""
    return [
        str(row[0]) for row in sql.execute(
            """select f.id from facts f join source_documents d on d.id = f.document_id
                where d.doc_number = %s""",
            (number,),
        ).fetchall()
    ]


def shown(client, sql, number):  # noqa: F811
    page = body(client.get(INBOX))
    return [fact_id for fact_id in inbox_rows_of(sql, number) if f'data-fact="{fact_id}"' in page]


def total_in_p_and_l(sql, number):  # noqa: F811
    return sql.execute(
        """select coalesce(sum(f.amount), 0)
             from facts f join source_documents d on d.id = f.document_id
            where d.doc_number = %s and f.superseded_at is null
              and f.allocation <> 'split'""",
        (number,),
    ).fetchone()[0]


def test_a_row_sorted_out_in_a_closed_month_leaves_the_inbox(
    client, web_env, sql, counterparty, item, units, invoices_removed,  # noqa: F811
    payruns_restored,  # noqa: F811
):
    """Разобрана в закрытом месяце — в инбоксе нет ни её, ни её сторно."""
    login_as(client, "accountant")
    fact_id = network_invoice(client, sql, counterparty, units, "CLS-1")
    approve_june(client, web_env)
    login_as(client, "accountant")
    assert shown(client, sql, "CLS-1") == [fact_id], "предохранитель: строки нет в инбоксе"

    answer = client.post(f"/inbox/{fact_id}/classify/", {"item": item, "unit": units["BG1"]})
    assert "failed" not in answer["Location"], answer["Location"]
    assert "moved=" in answer["Location"], "предохранитель: месяц не закрыт"

    assert shown(client, sql, "CLS-1") == [], "разобранная строка осталась в инбоксе"
    assert total_in_p_and_l(sql, "CLS-1") == Decimal("24000.00"), "деньги разошлись"
    # Второй раз её не разобрать: для разбора по номеру она тоже не ждёт.
    again = client.post(f"/inbox/{fact_id}/classify/", {"item": item, "unit": units["BG1"]})
    assert again.status_code == 404, again.status_code


def test_a_split_row_sorted_out_in_a_closed_month_leaves_the_inbox(
    client, web_env, sql, counterparty, item, units, invoices_removed,  # noqa: F811
    payruns_restored,  # noqa: F811
):
    """Разнесённая строка закрытого месяца: доли на месте, в инбоксе пусто."""
    login_as(client, "accountant")
    fact_id = network_invoice(client, sql, counterparty, units, "CLS-2")
    assert client.post("/expenses/split/", {"fact": fact_id, "evenly": "1"}).status_code == 302
    approve_june(client, web_env)
    login_as(client, "accountant")

    answer = client.post(f"/inbox/{fact_id}/classify/", {"item": item})
    assert "failed" not in answer["Location"], answer["Location"]
    assert shown(client, sql, "CLS-2") == [], "разобранная строка или её сторно в инбоксе"
    assert total_in_p_and_l(sql, "CLS-2") == Decimal("24000.00")
    assert shares_of(sql, "CLS-2"), "доли пропали"


def test_the_closing_check_reads_the_same_rule(
    client, web_env, sql, counterparty, item, units, invoices_removed,  # noqa: F811
    payruns_restored,  # noqa: F811
):
    """Готовность месяца и счётчик панели берут строки из того же правила."""
    from web.suppliers import waiting_for_an_article

    login_as(client, "accountant")
    fact_id = network_invoice(client, sql, counterparty, units, "CLS-3")
    approve_june(client, web_env)
    login_as(client, "accountant")
    client.post(f"/inbox/{fact_id}/classify/", {"item": item, "unit": units["BG1"]})

    ours = set(inbox_rows_of(sql, "CLS-3"))
    left = [row for row in waiting_for_an_article(None) if str(row.id) in ours]
    assert left == [], f"правило считает ждущими: {[(r.dedup_key, r.amount) for r in left]}"


def test_a_not_ours_paper_leaves_no_rows_in_the_inbox(
    client, sql, counterparty, units, invoices_removed,  # noqa: F811
):
    """Счёт без статьи, признанный «не нашим», не оставляет в очереди ни строки, ни сторно."""
    login_as(client, "accountant")
    fact_id = network_invoice(client, sql, counterparty, units, "CLS-4")
    document_id = sql.execute("select document_id from facts where id = %s",
                              (fact_id,)).fetchone()[0]
    answer = client.post(f"/invoices/{document_id}/not-ours/", {"why": "Соседний арендатор"})
    assert answer.status_code == 302, body(answer)[:300]
    assert shown(client, sql, "CLS-4") == [], "строки «не нашей» бумаги остались в инбоксе"
    assert total_in_p_and_l(sql, "CLS-4") == Decimal("0")
