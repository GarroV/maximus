"""ISO 20022 camt.053 — выписка по счёту, любой версии схемы.

Версии (001.02 … 001.13) отличаются пространством имён и мелочами, нужные нам
поля на одних и тех же местах, поэтому теги ищутся по локальному имени.
Библиотека не берётся: разбор — сотня строк, а готовые пакеты держит один
автор в версии 0.0.x (журнал блока facts, ресерч 26.10.2026).

Контрагент зависит от направления: у расхода это получатель (`Cdtr`), у
прихода — плательщик (`Dbtr`). Именно поэтому camt.053 разбирается кодом, а
не профилем «поле → тег» из `formats.yaml`.
"""
from __future__ import annotations

from django.utils.translation import gettext as _

from .model import IN, OUT, Line, Statement
from .values import amount, day


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node, *path):
    for step in path:
        if node is None:
            return None
        node = next((c for c in node if _local(c.tag) == step), None)
    return node


def _text(node, *path) -> str:
    found = _child(node, *path)
    return (found.text or "").strip() if found is not None else ""


def _account(node) -> str:
    """Номер счёта: IBAN или внутренний номер (`Othr/Id`) — что дал банк."""
    return _text(node, "Id", "IBAN") or _text(node, "Id", "Othr", "Id")


def read(root) -> Statement:
    from .reader import Unreadable

    statement = next((n for n in root.iter() if _local(n.tag) == "Stmt"), None)
    if statement is None:
        raise Unreadable(_("В camt.053 нет выписки (Stmt)."))
    acct = _child(statement, "Acct")
    lines = []
    entries = [n for n in statement if _local(n.tag) == "Ntry"]
    for number, entry in enumerate(entries, start=1):
        try:
            lines.append(_line(entry))
        except ValueError as error:
            raise Unreadable(_("Операция %(n)s: %(why)s.") % {"n": number, "why": error}) from error
    period = _child(statement, "FrToDt")
    return Statement(
        format="camt053",
        lines=lines,
        account=_account(acct) if acct is not None else "",
        currency=_text(acct, "Ccy") if acct is not None else "",
        bank=_text(acct, "Svcr", "FinInstnId", "Nm") if acct is not None else "",
        period_from=day(_text(period, "FrDtTm")) if period is not None else None,
        period_to=day(_text(period, "ToDtTm")) if period is not None else None,
    )


def _line(entry) -> Line:
    indicator = _text(entry, "CdtDbtInd")
    if indicator not in ("DBIT", "CRDT"):
        raise ValueError(_("непонятное направление %(value)s") % {"value": repr(indicator)})
    direction = OUT if indicator == "DBIT" else IN
    # Дата валюты — день, когда деньги реально ушли; дата проводки — запасная.
    on = day(_text(entry, "ValDt", "Dt") or _text(entry, "BookgDt", "Dt")
             or _text(entry, "BookgDt", "DtTm"))
    if on is None:
        raise ValueError(_("нет даты"))
    sum_ = amount(_text(entry, "Amt"))
    if sum_ is None:
        raise ValueError(_("нет суммы"))
    details = _child(entry, "NtryDtls", "TxDtls")
    party, party_acct = ("Cdtr", "CdtrAcct") if direction == OUT else ("Dbtr", "DbtrAcct")
    parties = _child(details, "RltdPties")
    party_node = _child(parties, party)
    # В версиях 001.08+ имя лежит глубже: Cdtr/Pty/Nm.
    name = _text(party_node, "Nm") or _text(party_node, "Pty", "Nm")
    account_node = _child(parties, party_acct)
    remittance = _child(details, "RmtInf")
    purpose = " ".join(
        (u.text or "").strip()
        for u in (remittance if remittance is not None else [])
        if _local(u.tag) == "Ustrd"
    )
    sum_node = _child(entry, "Amt")
    return Line(
        op_date=on, direction=direction, amount=abs(sum_),
        party_name=name,
        party_account=_account(account_node) if account_node is not None else "",
        purpose=purpose,
        # Ссылка банка — самый устойчивый ключ; EndToEndId ставит плательщик и
        # часто пишет туда NOTPROVIDED, поэтому ключом он не служит.
        bank_ref=_text(entry, "AcctSvcrRef") or _text(details, "Refs", "AcctSvcrRef"),
        currency=sum_node.get("Ccy", "") if sum_node is not None else "",
    )
