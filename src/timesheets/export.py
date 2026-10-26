"""Табель месяца как книга xlsx: «Скачать xlsx» в шапке сетки.

Файл — копия сетки, а не отчёт: те же строки, те же колонки типов часов и итог.
Числа пишутся числами, а не текстом: бухгалтер сложит столбец в своей таблице
без превращения «176,00» обратно в число.
"""
from __future__ import annotations

from datetime import date
from io import BytesIO

from django.utils.translation import gettext as _
from openpyxl import Workbook

from .grid import Grid

__all__ = ["grid_workbook"]


def grid_workbook(table: Grid, period: date) -> bytes:
    sheet = Workbook().active
    sheet.title = f"{period:%Y-%m}"
    sheet.append([
        _("Сотрудник"), _("Точка"),
        *[column.title for column in table.columns],
        _("Итого"), _("Норма"),
    ])
    for row in table.rows:
        sheet.append([
            row.employee, row.unit,
            *[float(row.cells.get(column.code, 0)) for column in table.columns],
            float(row.total), float(row.norm_hours),
        ])
    # Имя из справочника, начинающееся с «=», openpyxl записал бы формулой.
    for cells in sheet.iter_rows(min_col=1, max_col=2):
        for cell in cells:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    out = BytesIO()
    sheet.parent.save(out)
    return out.getvalue()
