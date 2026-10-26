"""Числа и даты так, как их пишут банки: «94.800,00», «05.06.2026», ISO."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext as _

CENT = Decimal("0.01")
# Выписка живёт в этих годах. Дата вне них — ошибка файла, а не операция:
# год 9999 завёл бы учётный месяц через восемь тысяч лет.
FIRST_YEAR, LAST_YEAR = 2000, 2100

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
        return _cents(Decimal(str(raw)), raw)
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
        head, tail = text.rsplit(mark, 1)
        # Тысячи не начинаются с нуля: «0.001» — дробь, а не тысяча.
        thousands_like = len(tail) == 3 and head not in ("", "0")
        decimal_mark = mark if (text.count(mark) == 1 and not thousands_like) else ""
    else:
        decimal_mark = ""
    thousands = {".", ","} - {decimal_mark}
    for sign in thousands:
        text = text.replace(sign, "")
    if decimal_mark == ",":
        text = text.replace(",", ".")
    try:
        value = _cents(Decimal(text), raw)
    except InvalidOperation as error:
        raise ValueError(_("не число: %(value)s") % {"value": repr(raw)}) from error
    return -value if negative else value


def _cents(value: Decimal, raw) -> Decimal:
    """До копеек — без округления: третий значащий знак после запятой — это
    ошибка разбора, и молча округлённая она стала бы суммой платежа."""
    if not value.is_finite():
        raise ValueError(_("не число: %(value)s") % {"value": repr(raw)})
    cents = value.quantize(CENT)
    if cents != value:
        raise ValueError(_("больше двух знаков после запятой: %(value)s") % {"value": repr(raw)})
    return cents


def day(raw) -> date | None:
    """Дата из ячейки или тега. Время отбрасывается: выписка живёт днями."""
    parsed = _parsed_day(raw)
    if parsed is not None and not FIRST_YEAR <= parsed.year <= LAST_YEAR:
        raise ValueError(_("дата вне %(first)s–%(last)s годов: %(value)s") % {
            "first": FIRST_YEAR, "last": LAST_YEAR, "value": repr(raw)})
    return parsed


def _parsed_day(raw) -> date | None:
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
