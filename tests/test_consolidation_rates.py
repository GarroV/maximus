"""Курсы консолидации: как курс выбирается и почему закрытый месяц не едет (T209).

Это деньги, которые партнёр и сеть читают как факт, поэтому проверки идут в
базу напрямую. Главное обещание эталона модуля 5: курс «хранится вместе с
отчётом. Пересчёт задним числом не меняет уже сохранённую консолидацию». Оно
проверяется прямо: месяц закрыт, курс поменялся — сумма закрытого месяца та же
до копейки, а у открытого — новая.

Курс в тестах вводится так, как его публикуют («1 EUR = 117,1834 RSD»), а
спрашивается обратная пара RSD → EUR: так проверяется, что пара ищется в обе
стороны, а не только в той, в какой её удобно считать.
"""
from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest

from conftest import (
    JULY,
    JUNE,
    T1,
    T2,
    U_BG1,
    USER_ADMIN,
    USER_DIRECTOR,
    USER_OTHER,
    as_app_user,
)
from facts_helpers import fact_payload, upsert_fact
from test_payrun_approval import reopen
from test_payrun_lifecycle import payrun_in, set_status

REASON = "пересчитать июнь: ошиблись в часах"


def rate(conn, base: str, quote: str, value: str, on: str = "2026-06-30", note: str = "") -> str:
    """Курс так, как его вводят: 1 base = value quote."""
    return conn.execute(
        """insert into fx_rates (base_currency, quote_currency, rate_date, rate, note)
           values (%s, %s, %s, %s, %s) returning id""",
        (base, quote, on, value, note),
    ).fetchone()[0]


def reported(conn, fact_id: str):
    """Сумма факта в валюте консолидации — так, как её видят отчёты."""
    return conn.execute(
        "select amount_report from pnl_lines where fact_id = %s", (fact_id,)
    ).fetchone()[0]


def fact(conn, amount: str, *, key: str, currency: str = "RSD", period: str = JUNE) -> str:
    fact_id, _ = upsert_fact(
        conn, fact_payload(unit=U_BG1, amount=amount, key=key, currency=currency, period=period)
    )
    return fact_id


def close_june(conn) -> str:
    """Закрыть июнь так, как его закрывает продукт: утверждением расчёта."""
    with as_app_user(conn, USER_DIRECTOR) as app:
        return payrun_in(app, "approved")


def fixes(conn, period: str = JUNE) -> dict:
    return dict(
        conn.execute(
            "select currency, fx_rate_id from period_fx_rates where tenant_id = %s and period = %s",
            (T1, period),
        ).fetchall()
    )


# --- выбор курса --------------------------------------------------------------

def test_rate_entered_as_published_is_divided_not_multiplied(db):
    """«1 EUR = 117,1834 RSD» хранится как опубликован, и RSD → EUR делится на курс.

    Без поиска пары в обратную сторону взялась бы старая июньская строка сида
    RSD → EUR (0,00854) — 8540,00 вместо 8533,63.
    """
    rate(db, "EUR", "RSD", "117.1834", on="2026-07-31")
    fact_id = fact(db, "1000000.00", key="m", period=JULY)

    assert reported(db, fact_id) == Decimal("8533.63")


def test_later_entry_of_the_same_date_wins_for_an_open_month(db):
    """Исправленный курс — новая строка той же даты, и открытый месяц берёт её."""
    rate(db, "EUR", "RSD", "100", on="2026-07-31")
    fact_id = fact(db, "1000.00", key="v", period=JULY)
    assert reported(db, fact_id) == Decimal("10.00")

    rate(db, "EUR", "RSD", "125", on="2026-07-31", note="исправлено")

    assert reported(db, fact_id) == Decimal("8.00")


def test_rate_after_the_month_end_is_not_used(db):
    """Курс берётся на последний день месяца, а не самый свежий вообще."""
    rate(db, "EUR", "RSD", "100", on="2026-07-31")
    rate(db, "EUR", "RSD", "200", on="2026-08-01")
    fact_id = fact(db, "1000.00", key="end", period=JULY)

    assert reported(db, fact_id) == Decimal("10.00")


def test_no_rate_reads_as_unknown_not_zero(db):
    """Курса нет — сумма пустая, а не ноль: ноль читался бы как посчитанный."""
    fact_id = fact(db, "1000.00", key="huf", currency="HUF")

    assert reported(db, fact_id) is None


# --- закрытый месяц не едет ---------------------------------------------------

def test_closed_month_keeps_its_rate_when_a_new_one_is_entered(db):
    """Главное обещание: пересчёт закрытого месяца даёт число дня закрытия."""
    fact_id = fact(db, "1000.00", key="closed")        # курс сида: 0.00854 на 30.06
    open_july = fact(db, "1000.00", key="open", period=JULY)
    close_june(db)
    assert reported(db, fact_id) == Decimal("8.54")

    rate(db, "RSD", "EUR", "0.009", on="2026-06-30", note="исправлено задним числом")

    assert reported(db, fact_id) == Decimal("8.54"), "закрытый месяц поехал вслед за курсом"
    assert reported(db, open_july) == Decimal("9.00"), "открытый месяц обязан взять новый курс"


def test_reopening_the_month_releases_the_rate_and_closing_fixes_the_new_one(db):
    """Открытие — явное решение с причиной (D021): месяц снова считает по живому курсу."""
    fact_id = fact(db, "1000.00", key="reopen")
    payrun_id = close_june(db)
    rate(db, "RSD", "EUR", "0.009", on="2026-06-30")
    assert reported(db, fact_id) == Decimal("8.54")

    with as_app_user(db, USER_DIRECTOR) as app:
        reopen(app, payrun_id, REASON)
    assert fixes(db) == {}, "у открытого месяца остался закреплённый курс"
    assert reported(db, fact_id) == Decimal("9.00")

    with as_app_user(db, USER_DIRECTOR) as app:
        set_status(app, payrun_id, "calculated", "approved")
    rate(db, "RSD", "EUR", "0.01", on="2026-06-30")

    assert reported(db, fact_id) == Decimal("9.00")


def test_month_closed_without_a_rate_takes_the_first_one_and_keeps_it(db):
    """Курса на день закрытия не было: первый введённый заполняет, следующий — нет."""
    fact_id = fact(db, "1000.00", key="huf", currency="HUF")
    close_june(db)
    assert reported(db, fact_id) is None

    rate(db, "EUR", "HUF", "400", on="2026-06-15")
    assert reported(db, fact_id) == Decimal("2.50")

    rate(db, "EUR", "HUF", "500", on="2026-06-30")
    assert reported(db, fact_id) == Decimal("2.50"), "заполненный курс закрытого месяца сдвинулся"


def test_closing_fixes_every_currency_of_the_month(db):
    """Закрепляются валюта учёта и валюты фактов месяца — каждая своей строкой."""
    fact(db, "1000.00", key="rsd")
    fact(db, "1000.00", key="huf", currency="HUF")
    close_june(db)

    fixed = fixes(db)
    assert set(fixed) == {"RSD", "HUF"}
    assert fixed["RSD"] is not None and fixed["HUF"] is None


# --- справочник и закрепление не правятся -------------------------------------

@pytest.mark.parametrize("statement", [
    "update fx_rates set rate = 1",
    "delete from fx_rates",
])
def test_rates_are_never_changed_in_place_even_by_the_owner(db, statement):
    """Правка на месте сдвинула бы закрытые месяцы, сославшиеся на строку."""
    with pytest.raises(psycopg.errors.RaiseException, match="не правится"):
        with db.transaction():
            db.execute(statement)


def test_fixed_rate_of_a_closed_month_is_not_rewritten(db):
    fact(db, "1000.00", key="guard")
    close_june(db)
    other = rate(db, "RSD", "EUR", "0.5", on="2026-01-01")

    with pytest.raises(psycopg.errors.RaiseException, match="не меняется"):
        with db.transaction():
            db.execute(
                "update period_fx_rates set fx_rate_id = %s where tenant_id = %s",
                (other, T1),
            )


# --- доступ: ролью приложения -------------------------------------------------

def test_partner_cannot_enter_a_rate(db):
    """Справочник общий: партнёр, вводящий курс, двигал бы цифру соседу."""
    with as_app_user(db, USER_DIRECTOR) as app:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with app.transaction():
                rate(app, "EUR", "RSD", "117")


def test_platform_admin_enters_a_rate_in_own_name_only(db):
    db.execute("insert into platform_admins (user_id) values (%s)", (USER_ADMIN,))
    with as_app_user(db, USER_ADMIN) as app:
        rate_id = rate(app, "EUR", "RSD", "117.18", note="НБС, средний курс")
        who = app.execute("select entered_by from fx_rates where id = %s", (rate_id,)).fetchone()[0]
        assert str(who) == USER_ADMIN

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with app.transaction():
                app.execute(
                    """insert into fx_rates (base_currency, quote_currency, rate_date, rate,
                                             entered_by)
                       values ('EUR', 'RSD', '2026-06-30', 117, %s)""",
                    (USER_DIRECTOR,),
                )


def test_fixed_rates_are_seen_only_by_their_partner_and_written_by_nobody(db):
    fact(db, "1000.00", key="iso")
    close_june(db)

    with as_app_user(db, USER_OTHER) as app:
        assert app.execute("select count(*) from period_fx_rates").fetchone()[0] == 0

    with as_app_user(db, USER_DIRECTOR) as app:
        assert app.execute("select count(*) from period_fx_rates").fetchone()[0] >= 1
        for statement in ("delete from period_fx_rates",
                          "update period_fx_rates set fixed_at = now()"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with app.transaction():
                    app.execute(statement)


def test_application_cannot_fix_rates_of_another_partner_directly(db):
    """Закрепление пишет только триггер закрытия; прямой вызов приложению закрыт."""
    db.execute(
        "insert into periods (tenant_id, period, status) values (%s, %s, 'closed')",
        (T2, JUNE),
    )
    db.execute("delete from period_fx_rates where tenant_id = %s", (T2,))

    with as_app_user(db, USER_DIRECTOR) as app:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with app.transaction():
                app.execute("select fix_period_rates(%s, %s)", (T2, JUNE))

    assert db.execute(
        "select count(*) from period_fx_rates where tenant_id = %s", (T2,)
    ).fetchone()[0] == 0
