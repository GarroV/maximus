"""Видеть ведомость и суммы расчёта — своё право (T236, `payroll.view`, `0275`).

Эталон «Роли и права» делает это отдельной строкой и не даёт её управляющему
региона: «больше точек — не значит больше денег». Стена стоит политикой чтения
на `pay_components` и `payslip_totals`, поэтому проверяется ролью `app_user`:
владелец схемы обходит RLS, и запрет, проверенный им, зелен всегда.

Сумм не видит только тот, у кого нет ни права смотреть, ни права, которое без
чтения сумм не исполнить (расчёт, утверждение, перенос разницы).
"""
from __future__ import annotations

import json

import pytest

from conftest import R_DIRECTOR, USER_DIRECTOR, as_app_user
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
