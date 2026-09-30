"""Люди, условия найма и справочники — три права, а не одно (T236, `0274`).

Эталон «Роли и права» разводит «Заводить и увольнять людей», «Менять ставки и
условия найма» и «Точки, юрлица, статьи расходов» по разным строкам. Прежде
всё это держало одно `directory.manage`, и партнёр не мог дать бухгалтеру
ставки, не дав заодно точки и юрлица.

Проверяется ролью `app_user`: владелец схемы обходит RLS, и запрет, проверенный
им, зелен всегда. Каждое право — парой «можно / нельзя» одним и тем же
запросом, иначе «нельзя никому» не отличить от «нельзя тому, кому не положено».
"""
from __future__ import annotations

import json

import psycopg
import pytest

from conftest import R_ADMIN, T1, U_NS1, USER_ADMIN, as_app_user

pytestmark = pytest.mark.usefixtures("db")

DENIED = psycopg.errors.InsufficientPrivilege


@pytest.fixture
def rows(db):
    employee = db.execute(
        """insert into employees (tenant_id, external_id, first_name, last_name)
           values (%s, 'split-1', 'Тест', 'Тестов') returning id""",
        (T1,),
    ).fetchone()[0]
    group = db.execute(
        """insert into employee_groups (tenant_id, code, title, scheme, ledger)
           values (%s, 'split-g', 'Группа', 'hourly', 'official') returning id""",
        (T1,),
    ).fetchone()[0]
    term = db.execute(
        """insert into employment_terms
               (tenant_id, employee_id, group_id, unit_id, base_rate, valid_from)
           values (%s, %s, %s, %s, 100, '2026-01-01') returning id""",
        (T1, employee, group, U_NS1),
    ).fetchone()[0]
    return {"employees": employee, "employment_terms": term, "units": U_NS1}


WRITES = {
    "employees": "update employees set last_name = 'Правленый' where id = %s",
    "employment_terms": "update employment_terms set base_rate = 555 where id = %s",
    "units": "update units set title = 'Другое название' where id = %s",
}

# Какое право какую таблицу открывает. Остальные две — закрыты.
OPENS = {
    "staff.manage": "employees",
    "terms.manage": "employment_terms",
    "directory.manage": "units",
}


@pytest.mark.parametrize("right", sorted(OPENS))
def test_each_right_opens_its_own_table_and_only_it(db, rows, right):
    db.execute("update roles set permissions = %s::jsonb where id = %s",
               (json.dumps([right]), R_ADMIN))
    with as_app_user(db, USER_ADMIN) as conn:
        for table, query in WRITES.items():
            conn.execute("savepoint attempt")
            if table == OPENS[right]:
                conn.execute(query, (rows[table],))
            else:
                with pytest.raises(DENIED):
                    conn.execute(query, (rows[table],))
            conn.execute("rollback to savepoint attempt")
