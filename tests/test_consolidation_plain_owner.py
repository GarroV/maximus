"""Закрепление курсов на базе без суперпользователя (T209).

Суперпользователь обходит RLS, и на обычной тестовой базе функция закрепления
видит всё при любых политиках. На управляемом Postgres владелец схемы — обычная
роль, и `force row level security` действует и на него. Здесь база накатывается
такой ролью до `0277`, в ней закрывается месяц, и только потом накатывается
`0278`: уже закрытый месяц обязан получить курс, а не молча остаться без него.
"""
from __future__ import annotations

import pytest

from conftest import JULY, JUNE, T1, U_BG1, USER_DIRECTOR, run_manage
from test_plain_owner import _plain_owner_cluster


@pytest.fixture(scope="module")
def owner_db():
    with _plain_owner_cluster("core", "0277") as env:
        import psycopg
        from psycopg.conninfo import make_conninfo

        from conftest import ADMIN_DSN

        admin_dsn = make_conninfo(ADMIN_DSN, dbname=env["dbname"])
        with psycopg.connect(admin_dsn) as admin:
            # Июль закрыт до появления 0278: курс сида (RSD → EUR на 30.06)
            # действует и на его конец.
            admin.execute(
                "insert into periods (tenant_id, period, status) values (%s, %s, 'closed')",
                (T1, JULY),
            )
            admin.commit()
        run_manage(env["owner_dsn"], "migrate", "--no-input")
        yield dict(env, admin_dsn=admin_dsn)


def test_month_closed_before_the_migration_gets_its_rate(owner_db):
    import psycopg

    with psycopg.connect(owner_db["admin_dsn"]) as admin:
        fixed = admin.execute(
            "select currency, fx_rate_id is not null from period_fx_rates "
            "where tenant_id = %s and period = %s",
            (T1, JULY),
        ).fetchall()

    assert fixed == [("RSD", True)], "закрытый до миграции месяц остался без курса"


def test_currency_hidden_from_the_closer_is_fixed_by_its_first_rate(owner_db):
    """Факт в валюте, скрытой от закрывающего (регистр), закрепляется тоже."""
    import psycopg
    from psycopg.types.json import Jsonb

    from conftest import R_DIRECTOR
    from core.db_types import register_enum_types
    from test_payrun_lifecycle import payrun_in

    with psycopg.connect(owner_db["admin_dsn"]) as admin:
        admin.execute("select upsert_fact(%s)", (Jsonb({
            "tenant_id": T1, "period": JUNE, "unit_id": U_BG1,
            "pnl_item_id": "b1111111-0000-0000-0000-000000000001", "ledger": "internal",
            "amount": "1000.00", "currency": "HUF", "title": "скрытый", "source": "manual",
            "dedup_key": "hidden-huf", "allocation": "direct"}),))
        admin.execute(
            "update roles set visible_ledgers = '{official,supplementary}' where id = %s",
            (R_DIRECTOR,),
        )
        admin.commit()

    with psycopg.connect(owner_db["app_dsn"]) as app:
        register_enum_types(app)
        app.execute("set role app_user")
        app.execute("select set_config('app.user_id', %s, false)", (USER_DIRECTOR,))
        payrun_in(app, "approved")
        app.commit()

    with psycopg.connect(owner_db["admin_dsn"]) as admin:
        admin.execute(
            "insert into fx_rates (base_currency, quote_currency, rate_date, rate) "
            "values ('EUR', 'HUF', '2026-06-30', 400)"
        )
        admin.commit()
        admin.execute(
            "insert into fx_rates (base_currency, quote_currency, rate_date, rate) "
            "values ('EUR', 'HUF', '2026-06-30', 500)"
        )
        admin.commit()
        amount = admin.execute(
            "select amount_report from pnl_lines where tenant_id = %s and currency = 'HUF'",
            (T1,),
        ).fetchone()[0]

    assert str(amount) == "2.50", "закрытый месяц взял не первый курс скрытой валюты"
