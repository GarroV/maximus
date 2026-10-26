"""Видеть ведомость и суммы расчёта — своё право (T236, `payroll.view`, `0275`).

Эталон «Роли и права» делает это отдельной строкой и не даёт её управляющему
региона: «больше точек — не значит больше денег». Стена стоит политикой чтения
на `pay_components` и `payslip_totals`, поэтому проверяется ролью `app_user`:
владелец схемы обходит RLS, и запрет, проверенный им, зелен всегда.

Те же суммы лежат ещё в двух местах, и стена стоит и там: след расчёта
(`payslip_steps`) и проведённый при утверждении ФОТ в фактах (`source =
'payroll'`), из которых собирается P&L.

Сумм не видит только тот, у кого нет ни права смотреть, ни права, которое без
чтения сумм не исполнить (расчёт, утверждение, перенос разницы).
"""
from __future__ import annotations

import json

import pytest

from conftest import R_DIRECTOR, T1, USER_DIRECTOR, as_app_user
from facts_helpers import fact_payload, upsert_fact
from test_payslip_visibility import make_payslip

pytestmark = pytest.mark.usefixtures("db")


def _seen(conn) -> tuple[int, int]:
    return (
        conn.execute("select count(*) from pay_components").fetchone()[0],
        conn.execute("select count(*) from payslip_totals").fetchone()[0],
    )


def _give(db, rights: list[str]) -> None:
    db.execute("update roles set permissions = %s::jsonb where id = %s",
               (json.dumps(rights), R_DIRECTOR))


@pytest.mark.parametrize("rights", [
    ["payroll.view"],
    ["payrun.calculate"],
    ["period.approve"],
    ["retro.post"],
])
def test_sums_are_seen_with_the_right_to_see_or_to_work_them(db, rights):
    make_payslip(db, "pv-1", [("official", "100.00")])
    _give(db, rights)
    with as_app_user(db, USER_DIRECTOR) as conn:
        assert _seen(conn) == (1, 1), f"с правами {rights} суммы обязаны быть видны"


def test_without_the_right_the_sums_are_not_there_at_all(db):
    """Как у регионального управляющего: часы и закрытие точки, ведомости нет."""
    make_payslip(db, "pv-2", [("official", "100.00")])
    _give(db, ["timesheet.edit", "unit.close"])
    with as_app_user(db, USER_DIRECTOR) as conn:
        assert _seen(conn) == (0, 0), "суммы расчёта видны роли без права их видеть"


def test_the_protection_is_the_policy_and_not_luck(db):
    """Сняли политику — та же роль видит суммы. Иначе «не видно» ничего не доказывает."""
    make_payslip(db, "pv-3", [("official", "100.00")])
    _give(db, ["timesheet.edit"])
    db.execute("savepoint before_damage")
    db.execute("drop policy payroll_view on pay_components")
    db.execute("drop policy payroll_view on payslip_totals")
    try:
        with as_app_user(db, USER_DIRECTOR) as conn:
            assert _seen(conn) == (1, 1), "без политики директор обязан видеть — тест не о том"
    finally:
        db.execute("rollback to savepoint before_damage")
    with as_app_user(db, USER_DIRECTOR) as conn:
        assert _seen(conn) == (0, 0)



# --- след расчёта и проведённый ФОТ: те же суммы, другие таблицы ----------------


def _step(db, payslip) -> None:
    db.execute(
        """insert into payslip_steps (tenant_id, payslip_id, position, code, title,
                                      applied_value)
           values (%s, %s, 1, 'hours.regular', 'Часы', 100.00)""",
        (T1, payslip),
    )


def _posted(db) -> None:
    """ФОТ, проведённый в факты при утверждении, — и рядом обычный расход."""
    upsert_fact(db, fact_payload(key="pv-payroll", source="payroll", amount="-100.00"))
    upsert_fact(db, fact_payload(key="pv-manual", source="manual"))


def _facts_seen(conn) -> list[str]:
    return sorted(row[0] for row in conn.execute("select dedup_key from facts").fetchall())


def test_the_calculation_trace_is_behind_the_same_wall(db):
    """`payslip_steps` хранит применённые значения и входы: это те же суммы."""
    _step(db, make_payslip(db, "pv-4", [("official", "100.00")]))
    _give(db, ["timesheet.edit", "unit.close"])
    with as_app_user(db, USER_DIRECTOR) as conn:
        assert conn.execute("select count(*) from payslip_steps").fetchone()[0] == 0, (
            "след расчёта виден роли без права видеть суммы"
        )
    _give(db, ["payroll.view"])
    with as_app_user(db, USER_DIRECTOR) as conn:
        assert conn.execute("select count(*) from payslip_steps").fetchone()[0] == 1


def test_posted_payroll_in_facts_is_hidden_and_the_rest_is_not(db):
    """P&L читает факты: без стены там ФОТ по точкам, мимо стены расчёта."""
    _posted(db)
    _give(db, ["timesheet.edit", "unit.close"])
    with as_app_user(db, USER_DIRECTOR) as conn:
        assert _facts_seen(conn) == ["pv-manual"], "проведённый ФОТ виден без права"
    _give(db, ["payroll.view"])
    with as_app_user(db, USER_DIRECTOR) as conn:
        assert _facts_seen(conn) == ["pv-manual", "pv-payroll"]


@pytest.mark.parametrize("table", ["payslip_steps", "facts"])
def test_the_new_walls_are_the_policies_and_not_luck(db, table):
    """Сняли политику — та же роль видит строки. Иначе «не видно» ничего не доказывает."""
    _step(db, make_payslip(db, "pv-5", [("official", "100.00")]))
    _posted(db)
    _give(db, ["timesheet.edit"])
    query = {
        "payslip_steps": "select count(*) from payslip_steps",
        "facts": "select count(*) from facts where source = 'payroll'",
    }[table]
    db.execute("savepoint before_damage")
    db.execute(f"drop policy payroll_view on {table}")
    try:
        with as_app_user(db, USER_DIRECTOR) as conn:
            assert conn.execute(query).fetchone()[0] == 1, "без политики видно — тест не о том"
    finally:
        db.execute("rollback to savepoint before_damage")
    with as_app_user(db, USER_DIRECTOR) as conn:
        assert conn.execute(query).fetchone()[0] == 0
