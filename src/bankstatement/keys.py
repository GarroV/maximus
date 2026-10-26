"""Ключ строки выписки: та же это операция, что уже загружена, или новая.

От ключа зависит, удвоит ли повторная загрузка деньги, — это ядро.

* Банк дал идентификатор операции — ключ из счёта и идентификатора. Он
  уникален только внутри банка (спецификации iBank и Raiffeisen), а счёт
  принадлежит одному банку, поэтому пара однозначна. Но у строк одного
  пакетного платежа (camt.053) идентификатор бывает общим, поэтому вторая и
  следующие строки с ним же получают номер повторения — так же, как ключ по
  содержимому. У первой номера нет: ключи, уже лежащие в базе, не меняются.
* Не дал — ключ из содержимого: счёт, дата, направление, сумма, счёт
  контрагента и назначение. Две **одинаковые** операции в одном файле — два
  события (две оплаты по 5 000 в один день бывают), поэтому к содержимому
  добавляется порядковый номер повторения. Та же пара в пересекающейся
  выписке получит те же номера и те же ключи.

Ключ — хеш, а не склеенная строка: назначение платежа бывает длинным, а в
ключе оно нужно только для сравнения.
"""
from __future__ import annotations

import hashlib
from collections import Counter

from .model import Line
from .values import norm


def line_keys(account: str, lines: list[Line]) -> list[str]:
    seen: Counter = Counter()
    keys = []
    for line in lines:
        if line.bank_ref:
            raw = "ref|" + norm(account) + "|" + line.bank_ref.strip()
            seen[raw] += 1
            if seen[raw] > 1:
                raw += "|" + str(seen[raw])
        else:
            content = "|".join((
                norm(account), line.op_date.isoformat(), line.direction,
                str(line.amount), norm(line.party_account), norm(line.purpose),
            ))
            seen[content] += 1
            raw = "sum|" + content + "|" + str(seen[content])
        keys.append(hashlib.sha256(raw.encode("utf-8")).hexdigest())
    return keys
