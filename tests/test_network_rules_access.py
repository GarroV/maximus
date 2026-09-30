"""Общие правила разнесения и привязки к точкам: кто может их писать (T221).

Проверяется ролью `app_user` (`as_app_user`): на владельца схемы политики не
действуют, и зелёный прогон ничего бы не значил.

Две вещи, и обе про запись, а не про чтение:

* общее правило (пустой `tenant_id`, `0270`) поставляется продуктом — читает
  его каждый вошедший, а правит, удаляет и заводит никто из партнёров. Иначе
  один партнёр мог бы забрать правило себе или стереть его, и у остальных ФОТ
  сети повис бы неразнесённым;
* привязка к точке делает человека «своим» (`0269`), поэтому урезанная роль не
  должна одной записью привязки открывать себе человека, которого не видит.
"""
from __future__ import annotations

import psycopg
import pytest

from conftest import (
    I_LABOUR,
    R_MANAGER,
    T1,
    T2,
    U_NS1,
    USER_ADMIN,
    USER_MANAGER,
    as_app_user,
)


def shared_rule(db, *, since: str = "2000-01-01") -> str:
    """Общее правило «поровну» на строку «Зарплата» — так, как его ставит `0270`."""
    return str(db.execute(
        """insert into allocation_rules
               (tenant_id, pnl_item_id, method, ledger, valid_from)
           values (null, %s, 'even', 'official', %s) returning id""",
        (I_LABOUR, since),
    ).fetchone()[0])


def rule_now(db, rule_id: str) -> tuple | None:
    return db.execute(
        "select tenant_id, method::text from allocation_rules where id = %s", (rule_id,),
    ).fetchone()


# --- общее правило ------------------------------------------------------------


def test_a_partner_cannot_take_the_shared_rule(db):
    """Роль с `directory.manage` не присваивает общее правило своему партнёру."""
    rule = shared_rule(db)
    with as_app_user(db, USER_ADMIN):
        db.execute("savepoint probe")
        try:
            db.execute(
                "update allocation_rules set tenant_id = %s where id = %s", (T1, rule),
            )
        except psycopg.errors.InsufficientPrivilege:
            db.execute("rollback to savepoint probe")
    assert rule_now(db, rule) == (None, "even"), "общее правило стало правилом партнёра"


def test_a_partner_cannot_change_the_shared_rule(db):
    rule = shared_rule(db)
    with as_app_user(db, USER_ADMIN):
        db.execute("savepoint probe")
        try:
            db.execute(
                "update allocation_rules set valid_to = '2000-02-01' where id = %s", (rule,),
            )
        except psycopg.errors.InsufficientPrivilege:
            db.execute("rollback to savepoint probe")
    assert db.execute(
        "select valid_to from allocation_rules where id = %s", (rule,),
    ).fetchone()[0] is None, "партнёр закрыл общее правило"


def test_a_partner_cannot_delete_the_shared_rule(db):
    rule = shared_rule(db)
    with as_app_user(db, USER_ADMIN):
        db.execute("savepoint probe")
        try:
            db.execute("delete from allocation_rules where id = %s", (rule,))
        except psycopg.errors.InsufficientPrivilege:
            db.execute("rollback to savepoint probe")
    assert rule_now(db, rule) is not None, "партнёр удалил общее правило"


def test_a_partner_cannot_insert_a_shared_rule(db):
    with as_app_user(db, USER_ADMIN):
        db.execute("savepoint probe")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(
                """insert into allocation_rules
                       (tenant_id, pnl_item_id, method, ledger, valid_from)
                   values (null, %s, 'even', 'official', '2000-01-01')""",
                (I_LABOUR,),
            )
        db.execute("rollback to savepoint probe")


def test_the_partner_still_reads_the_shared_rule(db):
    """Сужена запись, а не чтение: правило обязано находиться у каждого."""
    rule = shared_rule(db)
    with as_app_user(db, USER_ADMIN):
        seen = db.execute(
            "select count(*) from allocation_rules where id = %s", (rule,),
        ).fetchone()[0]
    assert seen == 1, "общее правило пропало из вида партнёра"


def test_two_shared_rules_of_one_line_cannot_overlap(db):
    """Двух общих ответов на одну строку за один срок быть не должно."""
    shared_rule(db, since="2000-01-01")
    with pytest.raises(psycopg.errors.ExclusionViolation):
        shared_rule(db, since="2020-01-01")


def test_partners_rules_of_one_line_still_live_side_by_side(db):
    """Своё правило партнёра рядом с общим — законно: оно перебивает общее."""
    shared_rule(db)
    for tenant in (T1, T2):
        db.execute(
            """insert into allocation_rules
                   (tenant_id, pnl_item_id, method, ledger, valid_from)
               values (%s, %s, 'even', 'official', '2020-01-01')""",
            (tenant, I_LABOUR),
        )


# --- привязка к точке ---------------------------------------------------------


def manager_may_manage_directory(db) -> None:
    """Урезанной роли дать право вести справочник — случай, который ловим.

    Без права запись отвергла бы политика права, и проверка зеленела бы по
    чужой причине.
    """
    db.execute(
        """update roles set permissions = permissions || '["directory.manage"]'::jsonb
            where id = %s""",
        (R_MANAGER,),
    )


def new_person(db, key: str) -> str:
    return str(db.execute(
        """insert into employees (tenant_id, external_id, first_name, last_name)
           values (%s, %s, 'Чужой', 'Человек') returning id""",
        (T1, key),
    ).fetchone()[0])


def test_a_restricted_role_cannot_open_a_stranger_by_binding_him(db):
    """Управляющий NS1 не делает «своим» человека, которого не видит."""
    manager_may_manage_directory(db)
    stranger = new_person(db, "stranger-1")
    with as_app_user(db, USER_MANAGER):
        assert not db.execute(
            "select app_employee_is_visible(%s, %s)", (T1, stranger),
        ).fetchone()[0], "человек виден и без привязки — проверять нечего"
        db.execute("savepoint probe")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(
                """insert into employee_units (tenant_id, employee_id, unit_id, valid_from)
                   values (%s, %s, %s, '2020-01-01')""",
                (T1, stranger, U_NS1),
            )
        db.execute("rollback to savepoint probe")
        assert not db.execute(
            "select count(*) from employees where id = %s", (stranger,),
        ).fetchone()[0], "привязка открыла управляющему чужого человека"


def test_a_restricted_role_still_binds_its_own_person(db):
    """Сторож от обратного: своего человека управляющий к своей точке привязывает."""
    manager_may_manage_directory(db)
    mine = new_person(db, "mine-1")
    group = db.execute(
        """insert into employee_groups (tenant_id, code, title, scheme, ledger)
           values (%s, 'net-g', 'Группа', 'hourly', 'official') returning id""",
        (T1,),
    ).fetchone()[0]
    db.execute(
        """insert into employment_terms
               (tenant_id, employee_id, group_id, unit_id, base_rate, valid_from)
           values (%s, %s, %s, %s, 100, '2020-01-01')""",
        (T1, mine, group, U_NS1),
    )
    with as_app_user(db, USER_MANAGER):
        db.execute(
            """insert into employee_units (tenant_id, employee_id, unit_id, valid_from)
               values (%s, %s, %s, '2020-01-01')""",
            (T1, mine, U_NS1),
        )
