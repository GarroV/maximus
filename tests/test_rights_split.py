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


# --- все шесть таблиц и все три записи: вставка, правка, удаление ---------------
#
# Политики 0274 стоят отдельно на insert, update и delete. Проверка одной правки
# зеленела бы и тогда, когда удаление осталось под старым правом. Отказ бывает
# двух видов: вставка и правка падают на `with check`, а удаление чужой строки
# политика просто не видит — ноль строк молча. Поэтому «нельзя» — это ошибка
# ИЛИ ноль затронутых строк, а «можно» — ровно одна.

OWNER = {
    "employees": "staff.manage",
    "employee_units": "staff.manage",
    "employment_terms": "terms.manage",
    "employee_allowances": "terms.manage",
    "positions": "terms.manage",
    "units": "directory.manage",
}


@pytest.fixture
def every_row(db, rows):
    employee, term = rows["employees"], rows["employment_terms"]
    group = db.execute(
        "select group_id from employment_terms where id = %s", (term,)
    ).fetchone()[0]
    allowance = db.execute(
        """insert into employee_allowances
               (tenant_id, employee_id, code, title, amount, valid_from)
           values (%s, %s, 'split-a', 'Надбавка', 10, '2026-01-01') returning id""",
        (T1, employee),
    ).fetchone()[0]
    position = db.execute(
        """insert into positions (tenant_id, code, title, group_id)
           values (%s, 'split-p', 'Должность', %s) returning id""",
        (T1, group),
    ).fetchone()[0]
    link = db.execute(
        """insert into employee_units (tenant_id, employee_id, unit_id, valid_from)
           values (%s, %s, %s, '2026-01-01') returning id""",
        (T1, employee, U_NS1),
    ).fetchone()[0]
    spare = db.execute(
        """insert into employees (tenant_id, external_id, first_name, last_name)
           values (%s, 'split-2', 'Второй', 'Тестов') returning id""",
        (T1,),
    ).fetchone()[0]
    return {**rows, "employee_allowances": allowance, "positions": position,
            "employee_units": link, "group": group, "spare": spare}


def _statements(table: str, ids: dict) -> dict[str, tuple[str, tuple]]:
    """Три записи на таблицу; удаление — строки, которую можно удалить без каскада."""
    row = ids[table]
    insert = {
        "employees": ("insert into employees (tenant_id, external_id, first_name, last_name)"
                      " values (%s, 'split-new', 'Новый', 'Тестов')", (T1,)),
        "employee_units": ("insert into employee_units (tenant_id, employee_id, unit_id,"
                           " valid_from) values (%s, %s, %s, '2026-02-01')",
                           (T1, ids["spare"], U_NS1)),
        "employment_terms": ("insert into employment_terms (tenant_id, employee_id, group_id,"
                             " unit_id, base_rate, valid_from)"
                             " values (%s, %s, %s, %s, 100, '2026-01-01')",
                             (T1, ids["spare"], ids["group"], U_NS1)),
        "employee_allowances": ("insert into employee_allowances (tenant_id, employee_id,"
                                " code, title, amount, valid_from)"
                                " values (%s, %s, 'split-b', 'Ещё', 5, '2026-01-01')",
                                (T1, ids["employees"])),
        "positions": ("insert into positions (tenant_id, code, title, group_id)"
                      " values (%s, 'split-q', 'Ещё', %s)", (T1, ids["group"])),
        "units": None,
    }[table]
    update = {
        "employees": "update employees set last_name = 'Правленый' where id = %s",
        "employee_units": "update employee_units set valid_from = '2026-01-02' where id = %s",
        "employment_terms": "update employment_terms set base_rate = 555 where id = %s",
        "employee_allowances": "update employee_allowances set amount = 11 where id = %s",
        "positions": "update positions set title = 'Другая' where id = %s",
        "units": "update units set title = 'Другое название' where id = %s",
    }[table]
    delete_row = ids["spare"] if table == "employees" else row
    statements = {
        "update": (update, (row,)),
        "delete": (f"delete from {table} where id = %s", (delete_row,)),
    }
    if insert is not None:
        statements["insert"] = insert
    return statements


def _effect(conn, query: str, params: tuple) -> int | None:
    """Сколько строк затронуто; None — база отказала ошибкой."""
    conn.execute("savepoint attempt")
    try:
        return conn.execute(query, params).rowcount
    except DENIED:
        return None
    finally:
        conn.execute("rollback to savepoint attempt")


@pytest.mark.parametrize("right", sorted(set(OWNER.values())))
def test_every_write_of_every_table_answers_to_its_own_right(db, every_row, right):
    db.execute("update roles set permissions = %s::jsonb where id = %s",
               (json.dumps([right]), R_ADMIN))
    wrong = []
    with as_app_user(db, USER_ADMIN) as conn:
        for table, owner in OWNER.items():
            for op, (query, params) in _statements(table, every_row).items():
                done = _effect(conn, query, params)
                if owner == right and done != 1:
                    wrong.append(f"{table}.{op}: с правом {right} не прошло ({done})")
                if owner != right and done not in (None, 0):
                    wrong.append(f"{table}.{op}: без своего права прошло под {right}")
    assert not wrong, "\n".join(wrong)
