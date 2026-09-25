"""Табель и ведомость говорят, куда идут деньги человека (T221, D055, #194).

Разнесение по точкам работает: у человека может быть несколько точек, и ФОТ
делится между ними, а у сетевого уходит на всю сеть. На экранах этого не было
видно вовсе — колонка точки показывала одну, ту, где стоит строка. Для
территориального управляющего это неправда в самом читаемом месте: половина его
зарплаты ложится в затраты соседней пиццерии, а ведомость называет одну.

Сетевой человек выглядел ещё хуже: пустая ячейка. Пустая ячейка читается как
«данных нет», а не как «на всю сеть», — и первое, что делает человек, это идёт
искать пропавшую точку.

Управляющему точки подпись не показывается, и это не недосмотр: набор точек
приезжает ему урезанным политикой `0267`, из двух точек он видит свою, набор
схлопывается в одну — о существовании чужой точки своего человека он знать не
должен (D023).
"""
from __future__ import annotations

from datetime import date

import psycopg
import pytest

from conftest import body, login_as
from test_closing_readiness import calculated  # noqa: F401

JUNE = date(2026, 6, 1)


@pytest.fixture
def sql(web_env):
    with psycopg.connect(web_env, autocommit=True) as conn:
        yield conn


@pytest.fixture(autouse=True)
def units_of_people_removed(sql):
    """Привязки не переживают тест: оставленные, они меняют расчёт у соседей."""
    yield
    sql.execute("delete from employee_units")


def somebody_with_hours(sql) -> tuple:
    """Человек со строкой табеля за июнь. Первый по ключу — чтобы не гадать."""
    found = sql.execute(
        """select e.id, e.last_name from employees e
             join timesheets t on t.employee_id = e.id and t.period = %s
            order by e.external_id limit 1""",
        (JUNE,),
    ).fetchone()
    assert found, "в сиде нет ни одной строки табеля за июнь"
    return found


def bind(sql, employee_id, code: str) -> None:
    sql.execute(
        """insert into employee_units (tenant_id, employee_id, unit_id, valid_from)
           select e.tenant_id, e.id, u.id, '2020-01-01'
             from employees e, units u
            where e.id = %s and u.code = %s""",
        (employee_id, code),
    )


def period_id(sql) -> str:
    return str(sql.execute(
        "select id from periods where period = %s", (JUNE,),
    ).fetchone()[0])


def row_of(html: str, name: str) -> str:
    """Строка таблицы с этим человеком — а не первое совпадение по всей странице."""
    start = html.find(name)
    assert start > 0, f"человека «{name}» на экране нет"
    opened = html.rfind("<tr", 0, start)
    closed = html.find("</tr>", start)
    return html[opened:closed]


# --- табель -------------------------------------------------------------------


def test_the_grid_says_between_which_units_the_money_is_split(client, sql):
    person, name = somebody_with_hours(sql)
    bind(sql, person, "NS1")
    bind(sql, person, "BG1")

    login_as(client, "director")
    line = row_of(body(client.get(f"/timesheets/{period_id(sql)}/")), name)

    assert "BG1" in line and "NS1" in line, f"обе точки человека не названы: {line[:400]}"


def test_the_grid_calls_a_person_without_units_the_whole_network(client, sql):
    person, name = somebody_with_hours(sql)
    sql.execute("update timesheets set unit_id = null where employee_id = %s", (person,))
    sql.execute(
        "update employment_terms set unit_id = null where employee_id = %s", (person,),
    )

    login_as(client, "director")
    line = row_of(body(client.get(f"/timesheets/{period_id(sql)}/")), name)

    assert "сеть" in line.lower() or "network" in line.lower(), (
        f"человек без точки показан пустой ячейкой: {line[:400]}"
    )


def test_a_person_on_one_unit_gets_no_note(client, sql):
    """Одна точка — подписи нет: она повторяла бы саму колонку.

    Сторож от обратной беды: подпись у каждого человека — это шум, из-за
    которого настоящее деление перестают замечать.
    """
    person, name = somebody_with_hours(sql)
    bind(sql, person, "NS1")

    login_as(client, "director")
    line = row_of(body(client.get(f"/timesheets/{period_id(sql)}/")), name)

    assert "делится" not in line.lower() and "split across" not in line.lower(), (
        f"подпись стоит там, где делить нечего: {line[:400]}"
    )


# --- ведомость ----------------------------------------------------------------


def test_the_payslip_row_says_the_money_is_split(client, sql, calculated):  # noqa: F811
    """Ведомость — то место, где сумму читают; там неправда о точке дороже всего."""
    person, name = sql.execute(
        """select e.id, e.last_name from employees e
             join payslips p on p.employee_id = e.id
            order by e.external_id limit 1"""
    ).fetchone()
    bind(sql, person, "NS1")
    bind(sql, person, "BG1")

    login_as(client, "director")
    line = row_of(body(client.get(calculated)), name)

    assert "BG1" in line and "NS1" in line, f"обе точки человека не названы: {line[:400]}"


# --- управляющему чужая точка не показывается ---------------------------------


def test_the_manager_is_not_told_about_the_foreign_unit_of_his_person(client, sql):
    """Подпись не превращается в способ узнать точку, которой не видно (D023).

    Проверяется на экране, а не только политикой: набор точек урезает база, и
    смысл проверки в том, что экран не обходит её вторым путём.
    """
    person, name = sql.execute(
        """select e.id, e.last_name from employees e
             join timesheets t on t.employee_id = e.id and t.period = %s
             join units u on u.id = t.unit_id
            where u.code = 'NS1' order by e.external_id limit 1""",
        (JUNE,),
    ).fetchone()
    bind(sql, person, "NS1")
    bind(sql, person, "BG1")

    login_as(client, "manager")
    line = row_of(body(client.get(f"/timesheets/{period_id(sql)}/")), name)

    assert "BG1" not in line, f"управляющему видна чужая точка его человека: {line[:400]}"
