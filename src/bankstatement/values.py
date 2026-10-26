"""Числа и даты так, как их пишут банки: «94.800,00», «05.06.2026», ISO."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext as _

CENT = Decimal("0.01")

# Слэшей нет намеренно: `01/06/2026` — и 1 июня, и 6 января, а угаданная не
# туда дата выглядит прочитанной из файла (то же правило у справочника статей).
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y%m%d")


def amount(raw) -> Decimal | None:
    """Сумма из ячейки. Пусто — `None`; не число — `ValueError`.

    Разделитель дроби угадывается по последнему знаку из двух: «94.800,00» и
    «94,800.00» дают одно и то же. Один знак и ровно три цифры после него —
    тысячи («1.840»), а не дробь: сумм с тремя знаками после запятой в
    выписке не бывает, а тысячи без копеек бывают.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        return Decimal(str(raw)).quantize(CENT)
    text = re.sub(r"[\s ']", "", str(raw))
    if not text:
        return None
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("-()+")
    dot, comma = text.rfind("."), text.rfind(",")
    if dot >= 0 and comma >= 0:
        decimal_mark = "." if dot > comma else ","
    elif dot >= 0 or comma >= 0:
        mark = "." if dot >= 0 else ","
        tail = text.rsplit(mark, 1)[1]
        decimal_mark = mark if (text.count(mark) == 1 and len(tail) != 3) else ""
    else:
        decimal_mark = ""
    thousands = {".", ","} - {decimal_mark}
    for sign in thousands:
        text = text.replace(sign, "")
    if decimal_mark == ",":
        text = text.replace(",", ".")
    try:
        value = Decimal(text).quantize(CENT)
    except InvalidOperation as error:
        raise ValueError(_("не число: %(value)s") % {"value": repr(raw)}) from error
    return -value if negative else value


def day(raw) -> date | None:
    """Дата из ячейки или тега. Время отбрасывается: выписка живёт днями."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    text = text[:10] if re.match(r"\d{4}-\d{2}-\d{2}T", text) else text.split(" ")[0]
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError(_("не дата: %(value)s") % {"value": repr(raw)})


def norm(text: str) -> str:
    """Для сравнения названий: регистр, пробелы и знаки не различаются."""
    return re.sub(r"[\W_]+", "", (text or "").casefold())
