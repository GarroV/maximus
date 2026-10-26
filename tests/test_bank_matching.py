"""Сопоставление строки выписки с внесённым: что решается само, а что ждёт человека.

Это ядро модуля, и ошибка здесь тихая: строка, сопоставленная не с тем
платежом, выглядит сведённой, а настоящий платёж остаётся висеть — и никто не
узнает, пока не разойдётся акт с поставщиком. Поэтому правило одно: **сама
решается только однозначная пара**. Всё, где выбор есть, уходит человеку
(эталон, модуль 7: «Ошибка автосопоставления дороже лишней минуты бухгалтера»).

Проверяется чистая функция, без базы: вход — строка и кандидаты, выход —
решение. Какие кандидаты вообще попадают на вход, проверяет `test_bank_statement`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from bankstatement.matching import DATE_WINDOW_DAYS, Candidate, choose

ON = date(2026, 6, 12)
EPS = "party-eps"
OTHER = "party-other"


def fact(ref, amount="24000.00", on=ON, party=EPS):
    return Candidate(kind="fact", ref=ref, amount=Decimal(amount), on=on, party=party)


def invoice(ref, amount="24000.00", on=date(2026, 6, 1), party=EPS):
    return Candidate(kind="invoice", ref=ref, amount=Decimal(amount), on=on, party=party)


def decide(amount="24000.00", on=ON, party=EPS, facts=(), invoices=()):
    return choose(Decimal(amount), on, party, list(facts), list(invoices))


# --- уже внесённый платёж ------------------------------------------------------


def test_the_only_payment_of_the_same_amount_and_date_is_matched():
    chosen = decide(facts=[fact("p1")])
    assert (chosen.kind, chosen.ref) == ("fact", "p1")


def test_a_payment_a_few_days_off_is_still_the_same_payment():
    """Дату денег человек вносит по памяти, банк проводит на день-два позже."""
    chosen = decide(facts=[fact("p1", on=date(2026, 6, 12 - DATE_WINDOW_DAYS))])
    assert chosen.kind == "fact"


def test_a_payment_outside_the_window_is_not_a_candidate():
    chosen = decide(facts=[fact("p1", on=date(2026, 6, 12 - DATE_WINDOW_DAYS - 1))])
    assert chosen.kind == "decide"


def test_a_kopeck_of_difference_is_another_payment():
    chosen = decide(facts=[fact("p1", amount="24000.01")])
    assert chosen.kind == "decide"


def test_two_equal_payments_are_not_guessed_between():
    """Два платежа по 24 000 одному поставщику — выбирает человек, а не порядок строк."""
    chosen = decide(facts=[fact("p1"), fact("p2")])
    assert chosen.kind == "decide"
    assert chosen.reason == "ambiguous"


def test_the_payee_settles_a_tie_when_only_one_payment_is_theirs():
    chosen = decide(facts=[fact("p1", party=OTHER), fact("p2")])
    assert (chosen.kind, chosen.ref) == ("fact", "p2")


def test_an_unknown_payee_does_not_settle_a_tie():
    chosen = decide(party=None, facts=[fact("p1", party=OTHER), fact("p2")])
    assert chosen.kind == "decide"


# --- неоплаченный счёт ----------------------------------------------------------


def test_the_only_unpaid_invoice_of_the_payee_for_this_amount_is_paid():
    chosen = decide(invoices=[invoice("inv1")])
    assert (chosen.kind, chosen.ref) == ("invoice", "inv1")


def test_an_invoice_of_another_payee_is_never_paid_by_itself():
    chosen = decide(invoices=[invoice("inv1", party=OTHER)])
    assert chosen.kind == "decide"


def test_an_unknown_payee_never_pays_an_invoice():
    """Без опознанного получателя совпадение суммы — случайность, а не основание."""
    chosen = decide(party=None, invoices=[invoice("inv1")])
    assert chosen.kind == "decide"


def test_two_unpaid_invoices_of_the_same_amount_wait_for_a_person():
    chosen = decide(invoices=[invoice("inv1"), invoice("inv2")])
    assert chosen.kind == "decide"
    assert chosen.reason == "ambiguous"


def test_an_invoice_issued_after_the_payment_is_not_what_it_paid():
    later = date(2026, 6, 12 + DATE_WINDOW_DAYS + 1)
    chosen = decide(invoices=[invoice("inv1", on=later)])
    assert chosen.kind == "decide"


def test_a_partial_amount_does_not_close_an_invoice_by_itself():
    chosen = decide(amount="10000.00", invoices=[invoice("inv1")])
    assert chosen.kind == "decide"


def test_an_entered_payment_wins_over_an_invoice():
    """Платёж уже внесён — второй платёж по счёту заводить нельзя: удвоится оплата."""
    chosen = decide(facts=[fact("p1")], invoices=[invoice("inv1")])
    assert (chosen.kind, chosen.ref) == ("fact", "p1")


def test_an_ambiguous_payment_does_not_fall_through_to_an_invoice():
    """Два внесённых платежа и один счёт: выбор между платежами — не повод платить счёт."""
    chosen = decide(facts=[fact("p1"), fact("p2")], invoices=[invoice("inv1")])
    assert chosen.kind == "decide"


def test_nothing_to_match_is_said_so():
    chosen = decide()
    assert (chosen.kind, chosen.reason) == ("decide", "none")
