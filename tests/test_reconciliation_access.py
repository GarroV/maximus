"""Расхождения сверки читает только тот, кто ведёт расчёт (миграция `0282`).

Строка расхождения — человек и две суммы по всем регистрам. Управляющему точки
регистры урезаны, и решать по расхождению он не вправе, поэтому граница стоит в
базе, а не только в представлении (D014). Проверяется ролью `app_user`: владелец
схемы политики обходит.
"""
from __future__ import annotations

from conftest import (
    JUNE,
    T1,
    USER_ACCOUNTANT,
    USER_MANAGER,
    as_app_user,
    as_directory_admin,
)


def _finding(db) -> str:
    with as_directory_admin(db):
        employee = db.execute(
            """insert into employees (tenant_id, external_id, first_name, last_name)
               values (%s, 'recon-access', 'Тест', 'Тестов') returning id""",
            (T1,),
        ).fetchone()[0]
    record = db.execute(
        """insert into reconciliations (tenant_id, period, file_name)
           values (%s, %s, 'test.xlsx') returning id""",
        (T1, JUNE),
    ).fetchone()[0]
    return str(db.execute(
        """insert into reconciliation_findings
               (tenant_id, reconciliation_id, employee_id, component, ours, theirs)
           values (%s, %s, %s, 'hours.regular', 100, 90) returning id""",
        (T1, record, employee),
    ).fetchone()[0])


def _sees(db, user: str, finding: str) -> bool:
    with as_app_user(db, user) as conn:
        return conn.execute(
            "select count(*) from reconciliation_findings where id = %s", (finding,),
        ).fetchone()[0] == 1


def test_the_one_who_runs_payroll_reads_the_difference(db):
    finding = _finding(db)
    assert _sees(db, USER_ACCOUNTANT, finding)


def test_a_unit_manager_does_not_read_the_difference(db):
    finding = _finding(db)
    assert not _sees(db, USER_MANAGER, finding), (
        "управляющий точки читает расхождения с суммами по всем регистрам"
    )
