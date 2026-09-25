"""Человек, привязанный к точке, виден её управляющему (T221, D055, issue #194).

Разнесение по точкам (T197) сделало так, что затраты человека делятся между
всеми точками, к которым он привязан: территориальный управляющий на две
пиццерии, офисный сотрудник — на сеть. Деньги поехали, а видимость осталась
прежней.

**Что именно осталось прежним.** `app_employee_is_visible` (`0020`) спрашивает
только условия найма: свой — тот, у кого есть версия условий найма на видимой
точке. Привязка `employee_units`, по которой делятся деньги, в это правило не
входила вовсе. Отсюда дыра ровно в одну сторону: часть ФОТ человека ложится в
затраты точки, а её управляющий этого человека в справочнике не находит — ни
имени, ни ставки. Спросить, почему его P&L вырос, ему не у кого.

Проверяется **ролью `app_user`**, а не владельцем схемы: на владельца политики
не действуют (а суперпользователь обходит даже `force row level security`), и
зелёный прогон означал бы только то, что запрос выполнился.

Проверяется и запись, не только чтение: ограничение на чтение без такого же на
запись обходится вставкой, и «не вижу» превратилось бы в «не вижу, но правлю».
"""
from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from conftest import as_app_user, body, login_as

LIST = "/directory/employees/"


@pytest.fixture
def sql(web_env):
    """Прямое соединение владельцем — только чтобы готовить и убирать состояние."""
    with psycopg.connect(web_env, autocommit=True) as conn:
        yield conn


@pytest.fixture
def rls(web_env):
    """Соединение для проверок доступа: своя транзакция, роль `app_user`.

    Отдельно от `sql`: `set local role` действует до конца транзакции, а в
    `autocommit` каждый оператор — своя транзакция, и проверка молча пошла бы
    владельцем схемы.
    """
    with psycopg.connect(web_env) as conn:
        yield conn
        conn.rollback()


@pytest.fixture(autouse=True)
def units_of_people_removed(sql):
    """Привязки к точкам не переживают тест.

    Та же уборка, что в `test_payroll_across_units` и `test_employee_units_screen`:
    оставленная строка меняет расчёт у соседних файлов — ведомость начинает
    делиться по точкам там, где тест этого не заводил.
    """
    yield
    sql.execute("delete from employee_units")


# Точка управляющего сида и точка, которой он не видит. Обе из `seed_dev.UNITS`;
# берутся именами, а не «первой попавшейся», потому что смысл теста — именно в
# том, что одна своя, а другая чужая.
MINE, ALIEN, THIRD = "NS1", "BG1", "NS2"


def somebody_of(sql, code: str) -> tuple:
    """Человек, чьи условия найма стоят на этой точке. Первый по ключу — не гадая."""
    found = sql.execute(
        """select e.id, e.external_id from employees e
             join employment_terms t on t.employee_id = e.id
             join units u on u.id = t.unit_id
            where u.code = %s order by e.external_id limit 1""",
        (code,),
    ).fetchone()
    assert found, f"в сиде нет человека с условиями найма на точке {code}"
    return found


def bind(sql, employee_id, code: str, *, share=None) -> None:
    """Привязать человека к точке — тем же, что пишет экран кадров."""
    sql.execute(
        """insert into employee_units (tenant_id, employee_id, unit_id, share, valid_from)
           select e.tenant_id, e.id, u.id, %s, '2020-01-01'
             from employees e, units u
            where e.id = %s and u.code = %s""",
        (share, employee_id, code),
    )


def boss_of(sql, code: str) -> str:
    """Управляющий этой точки — тот, у кого список точек ограничен ею."""
    found = sql.execute(
        """select m.user_id from memberships m
             join units u on u.id = any(m.unit_ids)
            where u.code = %s and m.unit_ids is not null limit 1""",
        (code,),
    ).fetchone()
    assert found, f"в сиде нет управляющего с ограниченным списком точек на {code}"
    return str(found[0])


def visible_to(conn, user_id: str, employee_id) -> bool:
    with as_app_user(conn, user_id):
        return bool(
            conn.execute(
                "select count(*) from employees where id = %s", (employee_id,),
            ).fetchone()[0]
        )


# --- 1. Привязка к точке делает человека своим --------------------------------


def test_a_person_bound_to_my_unit_is_mine_even_if_hired_elsewhere(rls, sql):
    """Часть ФОТ легла на мою точку — значит человек виден мне (D055).

    Условия найма у него на чужой точке, привязка — на моей. Сегодня деньги
    делятся по привязке, а справочник отвечает по условиям найма: управляющий
    видит в своём P&L чужую зарплату и не может узнать даже имени.
    """
    person, _key = somebody_of(sql, ALIEN)
    boss = boss_of(sql, MINE)

    # Предохранитель от зелёного по чужой причине: до привязки человек не виден.
    # Без него проверка зеленела бы и тогда, когда управляющий видит всех подряд.
    assert not visible_to(rls, boss, person), (
        "человек с условиями найма на чужой точке виден управляющему и без привязки"
    )

    bind(sql, person, MINE)
    assert visible_to(rls, boss, person), (
        "человек привязан к точке управляющего, его ФОТ делится на неё, "
        "а в справочнике его нет"
    )


def test_the_territorial_manager_is_seen_by_both_of_his_units(rls, sql):
    """Человек на двух точках — свой для обеих, а не только для той, где оформлен.

    Ровно случай владельца: «управляющий может быть на несколько пиццерий
    один». Деньги его делятся между обеими (T197), и обе обязаны знать, чью
    зарплату они несут.
    """
    person, _key = somebody_of(sql, MINE)
    bind(sql, person, MINE)
    bind(sql, person, ALIEN)

    assert visible_to(rls, boss_of(sql, MINE), person), (
        "человек перестал быть своим для точки, где оформлен"
    )
    assert visible_to(rls, _a_manager_of(sql, ALIEN), person), (
        "вторая точка человека несёт его ФОТ и не видит его самого"
    )


def test_the_network_person_stays_visible_to_everyone(rls, sql):
    """Точки в условиях найма нет — человек сетевой и виден каждому.

    Так было и до T221: `app_unit_is_visible` читает пустую точку как «строка
    ничья, видна всем в тенанте» (`0011`). Проверка стоит здесь сторожем —
    сужать доступ этой задачей не просили, а правка правила «свой человек» это
    ровно то место, где сужение прошло бы молча.
    """
    person, _key = somebody_of(sql, ALIEN)
    sql.execute(
        "update employment_terms set unit_id = null where employee_id = %s", (person,),
    )
    assert visible_to(rls, boss_of(sql, MINE), person), (
        "сетевой человек пропал из справочника управляющего"
    )


# --- 2. Чужой доступ от этого не расширился -----------------------------------


def test_a_manager_of_another_unit_gets_nothing_from_my_binding(rls, sql):
    """Привязка к моей точке не открывает человека управляющему соседней.

    Иначе «виден тем, к чьей точке привязан» тихо превратилось бы в «виден
    всем, у кого есть хоть одна привязка».
    """
    person, _key = somebody_of(sql, THIRD)
    bind(sql, person, MINE)

    assert not visible_to(rls, _a_manager_of(sql, ALIEN), person), (
        "управляющий чужой точки видит человека, привязанного не к нему"
    )


def _a_manager_of(sql, code: str) -> str:
    """Завести управляющего этой точки по образцу управляющего из сида.

    В сиде управляющий один и стоит на `NS1`; второго приходится заводить
    здесь. Пропускать проверку, когда его нет, нельзя — «чужой доступ не
    расширился» и есть половина смысла задачи, и молчаливый пропуск оставил бы
    её непроверенной.
    """
    tenant, role, password = sql.execute(
        """select m.tenant_id, m.role_id, u.password from memberships m
             join users u on u.id = m.user_id
            where m.unit_ids is not null limit 1"""
    ).fetchone()
    # Имя с суффиксом: соединение `sql` идёт в `autocommit`, то есть заведённый
    # человек переживает тест и достаётся следующему. Чистить его в конце —
    # значит держать уборку в двух местах; дешевле не сталкиваться именами.
    new_user = sql.execute(
        """insert into users (id, username, full_name, password, is_active)
           values (gen_random_uuid(), %s, 'Управляющий соседней точки', %s, true)
           returning id""",
        (f"manager_{code.lower()}_{uuid4().hex[:8]}", password),
    ).fetchone()[0]
    sql.execute(
        """insert into memberships (id, tenant_id, user_id, role_id, unit_ids)
           select gen_random_uuid(), %s, %s, %s, array[u.id] from units u where u.code = %s""",
        (tenant, new_user, role, code),
    )
    return str(new_user)


# --- 3. Видимость — это не право писать ---------------------------------------


def test_the_manager_still_cannot_rewrite_the_person_he_now_sees(rls, sql):
    """Увидел — не значит правит: запись закрыта правом, а не видимостью.

    Ограничение на чтение без такого же на запись обходится вставкой, поэтому
    проверяется именно `update`, а не только `select`.
    """
    person, _key = somebody_of(sql, ALIEN)
    boss = boss_of(sql, MINE)
    bind(sql, person, MINE)

    with as_app_user(rls, boss):
        assert rls.execute(
            "select count(*) from employees where id = %s", (person,),
        ).fetchone()[0] == 1
        rls.execute("savepoint probe")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rls.execute(
                "update employees set last_name = 'переименован управляющим' where id = %s",
                (person,),
            )
        rls.execute("rollback to savepoint probe")


def test_the_manager_cannot_bind_a_stranger_to_his_unit_to_see_him(rls, sql):
    """Привязка — не лазейка: завести её может только тот, кто ведёт справочники.

    Иначе управляющий открывал бы себе любого человека сети одной вставкой.
    """
    person, _key = somebody_of(sql, ALIEN)
    boss = boss_of(sql, MINE)
    tenant, unit_id = sql.execute(
        "select tenant_id, (select id from units where code = %s) from employees where id = %s",
        (MINE, person),
    ).fetchone()

    with as_app_user(rls, boss):
        rls.execute("savepoint probe")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rls.execute(
                "insert into employee_units (tenant_id, employee_id, unit_id, valid_from) "
                "values (%s, %s, %s, '2020-01-01')",
                (tenant, person, unit_id),
            )
        rls.execute("rollback to savepoint probe")


# --- 4. То же самое на экране, а не только в базе ------------------------------


def test_the_card_of_such_a_person_opens_and_hides_the_foreign_unit(client, web_env, sql):
    """Управляющий открывает карточку привязанного человека и не узнаёт чужую точку.

    Две стороны одного: экран обязан открыться (иначе видимость в базе ничего
    не даёт) и обязан промолчать о точке, которой управляющему не видно (D023).
    """
    person, _key = somebody_of(sql, ALIEN)
    sql.execute(
        "update employment_terms set unit_id = null where employee_id = %s", (person,),
    )
    bind(sql, person, MINE)
    bind(sql, person, ALIEN)

    login_as(client, "manager")
    answer = client.get(f"{LIST}{person}/")
    assert answer.status_code == 200, answer.status_code
    shown = body(answer)
    assert ALIEN not in shown, "в карточке видна точка, о которой управляющий знать не должен"
    assert MINE in shown, "в карточке не видно точки самого управляющего"
