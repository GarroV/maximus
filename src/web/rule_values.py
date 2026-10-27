"""Значение правила для экрана: с единицей, а не голым числом (issue #313).

`rules.show` отдаёт значение в том виде, в каком его вводят обратно, и менять его
нельзя: форма правки сверяет введённое с ним. Для чтения нужно другое: ставка
0,3505 человек читает как «35,05 %», а 51297.0 — как сумму в валюте страны.
Единицу определяет путь правила, а не вид числа: «1.0» бывает и коэффициентом
оплаты, и процентом, и часом, и угадать по значению нельзя. Незнакомое правило
показывается как есть — выдуманная единица хуже отсутствующей.

Только для показа. Ввод, сверка с рамкой и расчёт этим модулем не пользуются.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext as _

from .format import EMPTY, ledger_title, money

# Последний сегмент пути → что за величина. Суммы: основание взносов, минимум,
# цена часа, надбавка за норму, пороги расхождений в деньгах.
_MONEY_LEAVES = {
    "min_contribution_base", "tax_free_monthly", "min_hourly_rate", "amount_per_norm",
    "absolute",
}
_HOURS_LEAVES = {"full_day_hours", "reference_norm_hours", "norm_hours", "hours_per_day"}
_PERCENT_PATHS = ("rates.",)

# Пометка о проверке: слова, а не код.
_STATUS = {
    "unverified": lambda: _("не проверено"),
    "verified": lambda: _("проверено"),
}


def _number(raw: str) -> Decimal | None:
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _trim(number: Decimal) -> str:
    """Число без хвостовых нулей, с десятичным разделителем языка страницы."""
    from .format import separators

    _thousands, decimal = separators()
    text = f"{number.normalize():f}"
    return text.replace(".", decimal)


def display(path: str, raw: str, currency: str = "") -> str:
    """Значение правила для чтения. Пустое — прочерк."""
    if raw == "":
        return EMPTY
    if raw in ("true", "false"):
        return _("да") if raw == "true" else _("нет")
    leaf = path.rsplit(".", 1)[-1]
    if leaf == "ledger":
        return str(ledger_title(raw))
    if leaf == "status" and raw in _STATUS:
        return str(_STATUS[raw]())
    number = _number(raw)
    if number is None:
        return raw
    # Множитель нето → бруто — коэффициент, а не ставка: «70,1 %» читалось бы
    # как налог.
    is_rate = path.startswith(_PERCENT_PATHS) and leaf != "net_factor"
    if is_rate or leaf == "percent":
        scaled = number * 100 if is_rate else number
        return f"{_trim(scaled)} %"
    if leaf in _MONEY_LEAVES:
        return f"{money(number)} {currency}".strip()
    if leaf in _HOURS_LEAVES:
        return _("%(hours)s ч") % {"hours": _trim(number)}
    return _trim(number)
