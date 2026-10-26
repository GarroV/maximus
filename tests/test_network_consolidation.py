"""Консолидация сети: партнёры одного месяца в одной валюте (T209, модуль 5).

Сеть — все партнёры, в которых состоит смотрящий. Числа сложены так, чтобы
каждое отличалось от соседних: выручка, ФОТ и прибыль сходятся только если
каждая статья попала в свою колонку и пересчитана своим курсом.

Сид: Сербия (RSD, курс 1 RSD = 0,00854 EUR на 30.06) и второй партнёр (XXX).
Директор Сербии в тестах получает членство и во втором — это и есть «сеть».
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from conftest import (
    I_LABOUR,
    I_REVENUE,
    I_TAXES,
    I_TRANSFER,
    JUNE,
    R_ACCOUNTANT,
    R_MANAGER,
    R_OTHER,
    T1,
    T2,
    U_BG1,
    U_OTHER,
    USER_ACCOUNTANT,
    USER_DIRECTOR,
    USER_MANAGER,
    USER_OTHER,
    as_app_user,
)
from facts_helpers import fact_payload, upsert_fact
from reports.network import collect
from test_consolidation_rates import close_june, rate

PERIOD = date(2026, 6, 1)


def fact(conn, amount: str, *, item: str, key: str, tenant: str = T1, unit: str = U_BG1,
         currency: str = "RSD") -> None:
    upsert_fact(conn, fact_payload(tenant=tenant, unit=unit, item=item, amount=amount,
                                   key=key, currency=currency))


def serbia(conn) -> None:
    """Июнь Сербии: выручка, еда, зарплата с налогами и перевод, который не P&L."""
    fact(conn, "100000.00", item=I_REVENUE, key="rev")
    fact(conn, "-40000.00", item="b1111111-0000-0000-0000-000000000002", key="food")
    fact(conn, "-20000.00", item=I_LABOUR, key="labour")
    fact(conn, "-5000.00", item=I_TAXES, key="taxes")
    fact(conn, "-7777.00", item=I_TRANSFER, key="transfer")


def second_partner(conn) -> None:
    """Директор состоит и во втором партнёре; у того июнь в своей валюте."""
    conn.execute(
        "insert into memberships (tenant_id, user_id, role_id, unit_ids) values (%s, %s, %s, null)",
        (T2, USER_DIRECTOR, R_OTHER),
    )
    conn.execute(
        "insert into periods (tenant_id, period, status) values (%s, %s, 'open')", (T2, JUNE)
    )
    fact(conn, "1000.00", item=I_REVENUE, key="rev2", tenant=T2, unit=U_OTHER, currency="XXX")
    fact(conn, "-300.00", item=I_LABOUR, key="lab2", tenant=T2, unit=U_OTHER, currency="XXX")


def network(conn, user: str = USER_DIRECTOR):
    with as_app_user(conn, user) as app, app.cursor() as cursor:
        return collect(cursor, PERIOD)


def by_title(net) -> dict:
    return {p.title: p for p in net.partners}


def test_partner_row_is_converted_and_split_into_columns(db):
    serbia(db)

    row = by_title(network(db))["Dodo Serbia"]

    assert row.revenue == Decimal("854.00")
    assert row.payroll == Decimal("213.50"), "ФОТ — зарплата и взносы, положительным числом"
    assert row.profit == Decimal("298.90"), "прибыль — без перевода, который не P&L"
    assert row.rate.base == "RSD" and row.rate.value == Decimal("0.00854")
    assert row.rate.on == date(2026, 6, 30)
    assert row.complete


def test_network_sums_partners_in_one_currency(db):
    serbia(db)
    second_partner(db)
    rate(db, "EUR", "XXX", "0.5")      # 1 EUR = 0,5 XXX → 1000 XXX = 2000 EUR

    net = network(db)

    assert net.currency == "EUR"
    assert len(net.partners) == 2
    assert net.revenue == Decimal("2854.00")
    assert net.payroll == Decimal("813.50")
    assert net.profit == Decimal("1698.90")
    assert net.not_closed == 2


def test_partner_without_a_rate_is_shown_but_not_summed(db):
    """Сумма без части фактов выглядела бы полной, не будучи ею."""
    serbia(db)
    second_partner(db)

    net = network(db)
    other = by_title(net)["Другой партнёр"]

    assert other.unrated == 2 and not other.complete
    assert other.rate is None
    assert net.left_out == [other]
    assert net.revenue == Decimal("854.00")


def test_partner_with_another_report_currency_is_not_summed(db):
    serbia(db)
    second_partner(db)
    db.execute("update tenants set report_currency = 'USD' where id = %s", (T2,))
    rate(db, "USD", "XXX", "0.5")

    net = network(db)

    assert net.currency in ("EUR", "USD")
    assert len(net.summed) == 1 and len(net.left_out) == 1


def test_closed_month_of_the_network_does_not_move_with_a_new_rate(db):
    """Обещание эталона на уровне сети: июнь, закрытый вчера, сегодня тот же."""
    serbia(db)
    close_june(db)
    before = network(db)

    rate(db, "RSD", "EUR", "0.01")
    after = network(db)

    assert after.profit == before.profit == Decimal("298.90")
    assert after.partners[0].rate.fixed and after.partners[0].closed
    assert after.not_closed == 0


def test_each_partner_sees_only_itself(db):
    serbia(db)
    second_partner(db)

    assert [p.title for p in network(db, USER_OTHER).partners] == ["Другой партнёр"]


def test_accountant_without_a_ledger_sees_a_slice_that_is_not_summed(db):
    """Роль без регистра видит урезанную прибыль — партнёр не входит в итог."""
    serbia(db)
    db.execute(
        "update roles set visible_ledgers = '{official,supplementary}' where id = %s",
        (R_ACCOUNTANT,),
    )

    net = network(db, USER_ACCOUNTANT)

    row = by_title(net)["Dodo Serbia"]
    assert row.narrowed and not row.complete
    assert net.summed == []


def test_unit_manager_sees_a_slice_that_is_not_summed(db):
    """Управляющий одной точки видит её, а не партнёра целиком.

    Регистры ему открываются все — чтобы пометку давала именно точка.
    """
    serbia(db)
    db.execute(
        "update roles set visible_ledgers = '{official,supplementary,internal}' where id = %s",
        (R_MANAGER,),
    )

    net = network(db, USER_MANAGER)

    assert by_title(net)["Dodo Serbia"].narrowed
    assert net.summed == []


def test_director_with_every_ledger_and_unit_is_not_narrowed(db):
    serbia(db)

    assert not by_title(network(db))["Dodo Serbia"].narrowed
