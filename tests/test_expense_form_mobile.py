"""Форма «Внести расход» под управляющего с телефона (#311, #336).

Проверяется то, ради чего форму переделали: расход без статьи не теряется, а
уходит бухгалтеру во «Входящие»; служебные поля не стоят между управляющим и
кнопкой; фото чека — первое, что он видит. Всё идёт через экран, как у человека.
"""
from __future__ import annotations

import re

from conftest import body, login_as
from test_cash_expense import (  # noqa: F401
    URL, entry_key, facts_of, facts_removed, item, payload, tenant, units,
)
from test_directory import payruns_restored, sql  # noqa: F401

INBOX = "/inbox/"
MORE = '<details class="more"'
NOTE = "такси курьеру до BG1"


def test_an_expense_without_an_article_reaches_the_accountants_inbox(
    client, sql, units, facts_removed,  # noqa: F811
):
    """Справочник статей пуст, управляющий всё равно вносит расход (#311)."""
    login_as(client, "manager")
    key = entry_key()
    try:
        answer = client.post(URL, {
            "date": "2026-06-15", "amount": "800", "item": "", "note": NOTE,
            "entry_key": key,
        })
        assert answer.status_code == 302, body(answer)
    finally:
        client.post("/logout/")

    (stored,) = sql.execute(
        """select p.code, f.expense_item_id, f.unit_id::text, f.title
             from facts f join pnl_items p on p.id = f.pnl_item_id
            where f.dedup_key = %s""",
        (f"manual:cash:{key}",),
    ).fetchall()
    assert stored[0] == "unclassified", "расход без статьи не лёг в «Не разобрано»"
    assert stored[1] is None
    assert stored[2] == units["NS1"], "расход должен лечь на точку управляющего"
    assert stored[3] == NOTE

    login_as(client, "accountant")
    try:
        inbox = body(client.get(INBOX))
        assert NOTE in inbox, "бухгалтер не видит расход без статьи во «Входящих»"
        assert 'data-total="800.00"' in inbox
    finally:
        client.post("/logout/")


def test_a_manager_with_one_unit_does_not_see_the_service_fields(
    client, units, facts_removed,  # noqa: F811
):
    """Точка, касса, регистр и НДС — под закрытой раскрывашкой; точки нет вовсе."""
    login_as(client, "manager")
    try:
        page = body(client.get(URL))
    finally:
        client.post("/logout/")
    assert 'name="unit"' not in page, "точку управляющему с одной точкой не показываем"
    details = re.search(r'<details class="more"([^>]*)>', page)
    assert details is not None, "нет раскрывашки «Ещё поля»"
    assert "open" not in details.group(1), "у управляющего раскрывашка закрыта"
    assert page.index(MORE) < page.index('name="vat_rate"')
    assert page.index(MORE) < page.index('name="ledger"')


def test_the_accountants_service_fields_are_open_and_include_the_unit(
    client, units, facts_removed,  # noqa: F811
):
    login_as(client, "accountant")
    try:
        page = body(client.get(URL))
    finally:
        client.post("/logout/")
    details = re.search(r'<details class="more"([^>]*)>', page)
    assert details is not None and "open" in details.group(1)
    assert 'name="unit"' in page


def test_the_receipt_photo_comes_first_in_the_form(
    client, item, facts_removed,  # noqa: F811
):
    """Порядок: чек, сумма, статья, «кому и за что», дата, затем «Ещё поля»."""
    login_as(client, "manager")
    try:
        page = body(client.get(URL))
    finally:
        client.post("/logout/")
    order = [page.index(marker) for marker in (
        'name="receipt"', 'name="amount"', 'name="item"', 'name="note"',
        'name="date"', MORE,
    )]
    assert order == sorted(order), f"поля стоят не в том порядке: {order}"
    assert 'capture="environment"' in page, "нет плитки «Сфотографировать»"
    assert page.count('type="file"') == 2


def test_an_empty_article_list_says_what_to_do_instead_of_an_empty_select(
    client, facts_removed,  # noqa: F811
):
    login_as(client, "manager")
    try:
        page = body(client.get(URL))
    finally:
        client.post("/logout/")
    assert 'name="item"' not in page
    assert "бухгалтер разнесёт" in page


def test_an_expense_with_an_article_is_still_recorded_with_it(
    client, sql, item, units, facts_removed,  # noqa: F811
):
    login_as(client, "manager")
    key = entry_key()
    try:
        assert client.post(URL, payload(item, units, entry_key=key)).status_code == 302
    finally:
        client.post("/logout/")
    assert facts_of(sql, key)[0][11] == item
