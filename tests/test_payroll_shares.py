"""Арифметика деления ФОТ человека по точкам (`payrun.posting._shares`, D055).

Без базы: доли приходят уже прочитанными, проверяется только раскладка. Два
случая здесь — оба про деньги, которые ошибаются молча:

* все доли нулевые — делить не на что, и тихий возврат к точке строки табеля
  положил бы сумму туда, куда человека никто не ставил;
* путь «одна точка» и путь «несколько точек» обязаны округлять одинаково —
  иначе одна и та же сумма даёт в P&L разные копейки в зависимости от того,
  сколько у человека точек.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from payrun.errors import PayrunRefused
from payrun.posting import _shares


def test_all_zero_shares_are_refused_not_sent_to_the_timesheet_unit():
    with pytest.raises(PayrunRefused):
        _shares({"e": [(1, Decimal("0")), (2, Decimal("0"))]}, "e", 3, Decimal("10"))


@pytest.mark.parametrize("across", [
    {},                                   # набор не задан — точка строки
    {"e": [(1, None)]},                   # одна точка
    {"e": [(None, None)]},                # «вся сеть»
])
def test_every_path_rounds_to_cents(across):
    parts = _shares(across, "e", 7, Decimal("100.005"))
    assert [amount for _unit, amount in parts] == [Decimal("100.00")], parts


def test_the_split_path_rounds_the_same_way():
    parts = _shares({"e": [(1, None), (2, None)]}, "e", 7, Decimal("100.005"))
    assert sum(amount for _unit, amount in parts) == Decimal("100.00"), parts
