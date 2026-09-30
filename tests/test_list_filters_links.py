"""Отбор расходов и счетов ссылками, а не выпадающими списками (T224, D081).

Здесь проверяется то, что стоит дорого, если сломается молча:

- **отбор не предлагает недоступного** (D023). Управляющий точки не видит ни
  чужой точки, ни внутреннего регистра — ни строкой, ни ссылкой отбора. Кнопка с
  названием регистра, которого роль не видит, — уже сообщение о том, что он есть;
- **отборы комбинируются**: каждая ссылка несёт весь остальной отбор. Потерянный
  параметр не падает — он молча показывает не тот срез, и человек сверяет кассу с
  чужим числом;
- **выбранное — не ссылка**, и выпадающего списка на экране нет.

Как выглядит ряд, проверяется глазами в браузере, а не здесь.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import body, login_as
from test_cash_expense import item, tenant, units  # noqa: F401
from test_directory import payruns_restored, sql  # noqa: F401

EXPENSES, INVOICES = "/expenses/", "/invoices/"
JUNE = {"from": "2026-06-01", "to": "2026-06-30"}


def rows_of(page: str) -> dict[str, str]:
    """Ряды отбора: имя → разметка ряда."""
    block = re.search(r'<div class="filters">(.*?)\n</div>', page, re.S)
    assert block, "на странице нет блока отбора"
    return dict(re.findall(
        r'<div class="filters__row" data-filter="(\w+)">(.*?)\n  </div>', block.group(1), re.S,
    ))


def links_of(row: str) -> list[dict]:
    """Ссылки ряда разобранными параметрами адреса."""
    return [
        {k: v[0] for k, v in parse_qs(urlsplit(href.replace("&amp;", "&")).query).items()}
        | {"_path": urlsplit(href).path}
        for href in re.findall(r'<a [^>]*href="([^"]*)"', row)
    ]


def current_of(row: str) -> list[str]:
    return [t.strip() for t in re.findall(r'aria-current="true">([^<]*)<', row)]


def page_of(client, path, **params) -> str:
    response = client.get(path, params)
    assert response.status_code == 200, body(response)
    return body(response)


@pytest.mark.parametrize("path", [EXPENSES, INVOICES])
def test_no_dropdown_and_no_show_button(client, sql, path):  # noqa: F811
    """На списке нет `<select>` и кнопки «Показать»: нажатая ссылка и есть отбор."""
    login_as(client, "accountant")
    page = page_of(client, path)
    assert "<select" not in page
    assert "Показать</button>" not in page
    assert rows_of(page), "отбор пропал целиком"


def test_manager_is_offered_only_own_unit_and_ledgers(client, sql, units):  # noqa: F811
    """Управляющему не предложены ни чужая точка, ни внутренний регистр (D023)."""
    login_as(client, "manager")
    rows = rows_of(page_of(client, EXPENSES, **JUNE))
    # Одна точка — выбирать не из чего, ряда нет вовсе.
    assert "unit" not in rows
    offered = {link.get("ledger") for link in links_of(rows["ledger"])}
    assert "internal" not in offered
    assert "Внутренний" not in rows["ledger"]

    # Чужая точка в адресе: ряд появляется (снять отбор), но её имени в нём нет.
    rows = rows_of(page_of(client, EXPENSES, unit=units["NS2"], **JUNE))
    assert "NS2" not in rows["unit"]
    assert {link.get("unit") for link in links_of(rows["unit"])} <= {None, units["NS1"]}


def test_accountant_is_offered_every_unit_and_ledger(client, sql, units):  # noqa: F811
    """Бухгалтер видит все точки и все три регистра — ряд не урезан на всякий случай."""
    login_as(client, "accountant")
    rows = rows_of(page_of(client, EXPENSES, **JUNE))
    offered_units = {link.get("unit") for link in links_of(rows["unit"])}
    assert set(units.values()) <= offered_units
    offered = {link.get("ledger") for link in links_of(rows["ledger"])}
    assert {"official", "supplementary", "internal"} <= offered


def test_every_link_keeps_the_rest_of_the_filter(client, sql, units):  # noqa: F811
    """Ссылка меняет одно значение и несёт остальные: месяц и регистр не теряются."""
    login_as(client, "accountant")
    rows = rows_of(page_of(client, EXPENSES, ledger="official", **JUNE))
    for link in links_of(rows["unit"]):
        assert (link.get("ledger"), link.get("from"), link.get("to")) == (
            "official", JUNE["from"], JUNE["to"],
        ), link
    # Выбранное значение — текст, а не ссылка на само себя.
    assert current_of(rows["ledger"]) == ["Официальный"]
    assert all(link.get("ledger") != "official" for link in links_of(rows["ledger"]))
    # Месяц: соседние — ссылками с тем же регистром, выбранный — текстом.
    months = links_of(rows["period"])
    assert [(m["from"], m["ledger"]) for m in months] == [
        ("2026-05-01", "official"), ("2026-07-01", "official"),
    ]
    assert current_of(rows["period"]) == ["Июнь 2026"]


def test_item_is_picked_on_its_own_page_and_comes_back(client, sql, item):  # noqa: F811
    """Статья выбирается на отдельной странице, и ссылка возвращает весь отбор."""
    login_as(client, "accountant")
    rows = rows_of(page_of(client, EXPENSES, ledger="official", **JUNE))
    (pick,) = [link for link in links_of(rows["item"]) if link["_path"].endswith("/item/")]
    assert pick["ledger"] == "official"

    picker = page_of(client, pick["_path"], ledger="official", **JUNE)
    chosen = [
        link for link in links_of(re.search(r'<ul class="pick">(.*?)</ul>', picker, re.S).group(1))
        if link.get("item") == str(item)
    ]
    assert chosen and chosen[0]["_path"] == EXPENSES
    assert (chosen[0]["ledger"], chosen[0]["from"]) == ("official", JUNE["from"])

    back = rows_of(page_of(client, EXPENSES, item=str(item), ledger="official", **JUNE))
    assert current_of(back["item"]) == ["Вода"]


def test_invoice_state_row_marks_the_choice(client, sql):  # noqa: F811
    """Состояние оплаты — ряд: выбранное текстом, «все» снимает параметр целиком."""
    login_as(client, "accountant")
    rows = rows_of(page_of(client, INVOICES, state="unpaid", **JUNE))
    assert current_of(rows["state"]) == ["Только неоплаченные"]
    states = [link.get("state") for link in links_of(rows["state"])]
    assert states == [None, "paid"]
