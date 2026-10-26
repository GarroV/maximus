"""Текст в выгрузке остаётся текстом: названия из выписки банка и причины
людей не превращаются в формулы у того, кто откроет файл."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook

from reports.cells import safe_cell


@pytest.mark.parametrize("text", ["=1+1", "+7", "-x", "@SUM(A1)", "\tA", "\rA"])
def test_a_formula_start_gets_an_apostrophe(text):
    assert safe_cell(text) == "'" + text


@pytest.mark.parametrize("value", ["Komerc doo", "", None, Decimal("-5.00"), -3, date(2026, 6, 1)])
def test_plain_text_and_non_text_stay_as_they_are(value):
    assert safe_cell(value) == value


def test_every_text_cell_of_the_export_goes_through_it():
    from reports.export import _append

    sheet = Workbook().active
    _append(sheet, ["=HYPERLINK(\"x\")", Decimal("-1.00"), "Naziv"])
    values = [cell.value for cell in sheet[1]]
    assert values == ["'=HYPERLINK(\"x\")", Decimal("-1.00"), "Naziv"]
