"""С чем из внесённого свести строку выписки без человека.

**Само решается только однозначное.** Ошибка автосопоставления дороже лишней
минуты бухгалтера (эталон, модуль 7): строка, сведённая не с тем платежом,
выглядит закрытой, а настоящий платёж висит, и узнают об этом на сверке акта
с поставщиком. Поэтому при любом выборе решение уходит человеку.

Порядок важен:

1. **Уже внесённый платёж** (счёта или без счёта) той же суммы в окне дат.
   Он побеждает счёт: если платёж внесён, второй платёж по счёту удвоил бы
   оплату. Несколько таких — выбирает получатель, если он опознан и среди
   них ровно один его; иначе человек. И неоднозначность здесь **не
   проваливается** к счетам: «не знаю, какой из двух платежей» — не повод
   платить третье.
2. **Неоплаченный счёт** опознанного получателя с остатком ровно на сумму
   строки и датой не позже платежа. Без опознанного получателя счёт не
   оплачивается никогда: совпадение суммы у незнакомца — случайность.

Окно дат — потому что дату денег человек вносит по памяти или по дате
поручения, а банк проводит на день-два позже.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

DATE_WINDOW_DAYS = 5

FACT = "fact"
INVOICE = "invoice"
DECIDE = "decide"


@dataclass(frozen=True)
class Candidate:
    kind: str            # FACT — внесённый платёж; INVOICE — неоплаченный счёт
    ref: str             # ключ факта или номер документа
    amount: Decimal      # сумма платежа или остаток счёта
    on: date             # дата денег или дата счёта
    party: str | None    # контрагент


@dataclass(frozen=True)
class Choice:
    kind: str
    ref: str | None = None
    # single, single_of_party, invoice_of_party — решено само;
    # ambiguous — кандидатов больше одного; none — нет ни одного.
    reason: str = ""


def choose(amount: Decimal, on: date, party: str | None,
           facts: list[Candidate], invoices: list[Candidate],
           window: int = DATE_WINDOW_DAYS) -> Choice:
    span = timedelta(days=window)
    same = [f for f in facts if f.amount == amount and abs(f.on - on) <= span]
    if len(same) == 1:
        return Choice(FACT, same[0].ref, "single")
    if same:
        theirs = [f for f in same if party is not None and f.party == party]
        if len(theirs) == 1:
            return Choice(FACT, theirs[0].ref, "single_of_party")
        return Choice(DECIDE, reason="ambiguous")
    if party is None:
        return Choice(DECIDE, reason="none")
    payable = [i for i in invoices
               if i.party == party and i.amount == amount and i.on <= on + span]
    if len(payable) == 1:
        return Choice(INVOICE, payable[0].ref, "invoice_of_party")
    return Choice(DECIDE, reason="ambiguous" if payable else "none")
