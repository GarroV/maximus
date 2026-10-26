"""Строка выписки и выписка — одинаковые для любого формата банка."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

OUT = "out"   # деньги ушли со счёта
IN = "in"     # деньги пришли на счёт


@dataclass(frozen=True)
class Line:
    """Одна операция по счёту. Сумма всегда положительная, знак — в `direction`.

    Так, а не знаком суммы, потому что банки дают и то и другое: camt.053 и
    iBank — сумму и признак, Raiffeisen и таблицы — две колонки «на терет» и
    «в корист». Свести их к одной форме здесь дешевле, чем помнить о знаке
    в каждом потребителе.
    """

    op_date: date
    direction: str
    amount: Decimal
    party_name: str = ""
    party_account: str = ""
    purpose: str = ""
    # Идентификатор операции в банке («broj za reklamaciju», AcctSvcrRef).
    # Пусто — банк его не дал, и ключ строки собирается из её содержимого.
    bank_ref: str = ""
    currency: str = ""


@dataclass(frozen=True)
class Statement:
    format: str
    lines: list[Line]
    account: str = ""
    currency: str = ""
    bank: str = ""
    period_from: date | None = None
    period_to: date | None = None
    # Как поняты колонки: (заголовок в файле, поле строки) — показывается
    # человеку, чтобы ошибку разбора было видно до того, как она стала суммой.
    mapping: list[tuple[str, str]] = field(default_factory=list)
