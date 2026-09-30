"""Затраты человека делятся по его точкам, а не падают на одну (D055, #194, T197).

Решение владельца 27.08.2026, дословно: «управляющий может быть на несколько
пиццерий один… чтобы условный человек в Нови-Саде делился только по пиццериям
Нови-Сада, а в Белграде только на Белград», и про офис: «равномерно должно,
потому что офис на всех работает, вне зависимости».

Что было. У ведомости одна точка (`payslips.unit_id`), и перенос в P&L (T208)
клал весь ФОТ человека на неё. Для пиццамейкера это правда, для управляющего
двумя точками — нет: его зарплата целиком ложилась в затраты одной пиццерии, и
её P&L выглядел хуже соседнего без всякой причины.

Что теперь. У человека может быть **несколько** точек; его деньги делятся между
ними. Точек нет вовсе — это офис: он работает на всю сеть, и такая строка уходит
на разнесение общим правилом, как любой расход юрлица.

**Умолчание — поровну**, как и сказал владелец. Способ деления — настройка, но
пока в продукте один способ; второй («по выручке») уже существует у расходов и
подключается тем же полем, когда выручка появится.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import body, login_as
from test_closing_readiness import calculated  # noqa: F401
from test_directory import sql  # noqa: F401


@pytest.fixture(autouse=True)
def units_of_people_removed(sql):  # noqa: F811
    """Привязки к точкам не переживают тест.

    Без этой уборки они остаются в базе и меняют расчёт у **соседних** файлов:
    ведомость начинает делиться по точкам там, где тест этого не заводил, и
    сорок девять проверок расчёта падают, не понимая почему. Поймано полным
    прогоном: по отдельности каждый файл зелёный, вместе — нет.
    """
    yield
    sql.execute("delete from employee_units")


def approve(client, url):
    return client.post(url + "approve/", {"postpone_blockers": "1"}, follow=True)


def payroll_by_unit(sql) -> dict:  # noqa: F811
    rows = sql.execute(
        """select coalesce(u.code, 'сеть'), sum(f.amount)
             from facts f left join units u on u.id = f.unit_id
            where f.dedup_key like 'payrun:%%' and f.superseded_at is null
              and f.allocation <> 'split'
            group by 1"""
    ).fetchall()
    return {code: amount for code, amount in rows}


def put_on_units(sql, employee_key: str, codes: list[str]) -> None:  # noqa: F811
    """Привязать человека к точкам — так же, как это сделает экран кадров."""
    sql.execute(
        """delete from employee_units
            where employee_id = (select id from employees where external_id = %s)""",
        (employee_key,),
    )
    for code in codes:
        sql.execute(
            """insert into employee_units (tenant_id, employee_id, unit_id, valid_from)
               select e.tenant_id, e.id, u.id, '2020-01-01'
                 from employees e, units u
                where e.external_id = %s and u.code = %s""",
            (employee_key, code),
        )


def somebody(sql) -> str:  # noqa: F811
    return sql.execute(
        """select e.external_id from employees e
             join payslips p on p.employee_id = e.id
            order by e.external_id limit 1"""
    ).fetchone()[0]


# --- ядро ---------------------------------------------------------------------


def test_a_person_on_two_units_is_split_between_them(client, sql, calculated):  # noqa: F811
    """Человек на двух точках — его деньги делятся между ними."""
    who = somebody(sql)
    put_on_units(sql, who, ["BG1", "NS1"])

    login_as(client, "director")
    approve(client, calculated)

    by_unit = payroll_by_unit(sql)
    assert by_unit.get("BG1") and by_unit.get("NS1"), (
        f"деньги человека не разошлись по его точкам: {by_unit}"
    )


def test_the_split_keeps_the_total(client, sql, calculated):  # noqa: F811
    """Сумма по точкам равна полной стоимости — деление не теряет копеек."""
    put_on_units(sql, somebody(sql), ["BG1", "NS1", "NS2"])

    login_as(client, "director")
    approve(client, calculated)

    in_facts = -sum(payroll_by_unit(sql).values(), Decimal("0"))
    in_payslips = sql.execute(
        """select coalesce(sum(t.total_cost), 0) from payslip_totals t
             join payslips p on p.id = t.payslip_id
             join payruns r on r.id = p.payrun_id
            where r.period = '2026-06-01'"""
    ).fetchone()[0]
    assert in_facts == in_payslips, f"после деления {in_facts} против {in_payslips}"


def test_a_person_without_units_goes_to_the_whole_network(client, sql, calculated):  # noqa: F811
    """Точек нет — это офис: строка уходит на разнесение общим правилом.

    Владелец: «офис на всех работает, вне зависимости». Класть его на точку, где
    человек случайно числится, значило бы ухудшать её P&L без причины.
    """
    who = somebody(sql)
    put_on_units(sql, who, [])
    sql.execute(
        """update payslips set unit_id = null
            where employee_id = (select id from employees where external_id = %s)""",
        (who,),
    )

    login_as(client, "director")
    approve(client, calculated)

    network = sql.execute(
        """select count(*) from facts
            where dedup_key like 'payrun:%%' and superseded_at is null
              and (unit_id is null or allocation = 'allocated')"""
    ).fetchone()[0]
    assert network, "ФОТ без точки никуда не делся из одной точки"


def test_the_units_of_a_person_are_versioned(sql, web_env):  # noqa: F811
    """Привязка к точкам живёт с датами: перевод человека не переписывает прошлое.

    Тот же довод, что у условий найма (D020): закрытый месяц обязан считаться
    теми точками, которые были у человека тогда.
    """
    columns = {
        row[0] for row in sql.execute(
            "select column_name from information_schema.columns "
            "where table_name = 'employee_units'"
        ).fetchall()
    }
    assert {"valid_from", "valid_to"} <= columns, (
        f"привязка к точкам не версионируется: {sorted(columns)}"
    )


def test_the_office_payroll_does_not_hang_undistributed(client, sql, calculated):  # noqa: F811
    """Точек нет — ФОТ доезжает до точек, а не остаётся ждать разнесения.

    Строгая проверка рядом с той, что выше: та довольствуется «строка ушла из
    одной точки» и зеленеет даже тогда, когда факт остался `pending` навсегда.
    А остаться он может: правило разнесения ищется по контрагенту или по статье
    расхода, а у зарплатного факта нет ни того, ни другого.

    Разница видна только в P&L: сумма, висящая `pending`, в затраты точек не
    входит вовсе — то есть офис как не ложился на точки, так и не ложится, и
    жалоба issue #194 закрыта наполовину.
    """
    who = somebody(sql)
    put_on_units(sql, who, [])
    sql.execute(
        """update payslips set unit_id = null
            where employee_id = (select id from employees where external_id = %s)""",
        (who,),
    )

    login_as(client, "director")
    approve(client, calculated)

    waiting = sql.execute(
        """select count(*), coalesce(sum(f.amount), 0) from facts f
            where f.dedup_key like 'payrun:%%' and f.superseded_at is null
              and f.allocation = 'pending'"""
    ).fetchone()
    assert waiting[0] == 0, (
        f"ФОТ офиса висит неразнесённым: {waiting[0]} строк на {waiting[1]}"
    )

    # И доехал он до КАЖДОЙ точки, а не до одной: «офис на всех работает».
    # Без этой половины проверка зеленела бы и на правиле «всё на одну точку».
    got = sql.execute(
        """select count(distinct f.unit_id) from facts f
            where f.dedup_key like 'payrun:%%' and f.superseded_at is null
              and f.allocation = 'allocated' and f.unit_id is not null"""
    ).fetchone()[0]
    units = sql.execute("select count(*) from units").fetchone()[0]
    assert got == units, f"ФОТ сети дошёл до {got} точек из {units}"


# --- набор точек решает, а не точка строки табеля (D055, D085) ---------------
#
# Правило владельца (D085, из таблицы случаев issue #194): набор точек задан —
# он решает, куда идут деньги; одна точка — целиком на неё; несколько — делятся;
# явно «вся сеть» — делится между всеми точками партнёра; набор не задан вовсе
# — старое поведение, точка строки табеля. Исходная дыра #194 ровно здесь:
# офисный человек, записанный в табеле на случайную пиццерию, ложился на неё
# целиком.

TOLERANCE = Decimal("0.10")  # копейки деления: по строке P&L и регистру отдельно


def somebody_on(sql, code: str) -> str:  # noqa: F811
    """Человек, чья строка ведомости стоит на этой точке."""
    return sql.execute(
        """select e.external_id from employees e
             join payslips p on p.employee_id = e.id
             join units u on u.id = p.unit_id
            where u.code = %s
            order by e.external_id limit 1""",
        (code,),
    ).fetchone()[0]


def put_on_network(sql, employee_key: str) -> None:  # noqa: F811
    """Явный набор «вся сеть» — строка привязки без точки, как пишет экран."""
    put_on_units(sql, employee_key, [])
    sql.execute(
        """insert into employee_units (tenant_id, employee_id, unit_id, valid_from)
           select e.tenant_id, e.id, null, '2020-01-01'
             from employees e where e.external_id = %s""",
        (employee_key,),
    )


def costs_without(sql, employee_key: str) -> tuple[dict, Decimal]:  # noqa: F811
    """Стоимость остальных людей по точкам их строк — и полная стоимость этого."""
    rows = sql.execute(
        """select u.code, e.external_id = %s, sum(t.total_cost)
             from payslip_totals t
             join payslips p on p.id = t.payslip_id
             join payruns r on r.id = p.payrun_id
             join employees e on e.id = p.employee_id
             join units u on u.id = p.unit_id
            where r.period = '2026-06-01'
            group by 1, 2""",
        (employee_key,),
    ).fetchall()
    others = {code: amount for code, mine, amount in rows if not mine}
    own = sum((amount for _code, mine, amount in rows if mine), Decimal("0"))
    return others, own


def assert_lands(by_unit: dict, expected: dict) -> None:
    for code, amount in expected.items():
        got = -by_unit.get(code, Decimal("0"))
        assert abs(got - amount) <= TOLERANCE, (
            f"на {code} легло {got}, ожидалось {amount}; всё по точкам: {by_unit}"
        )


def test_the_network_set_beats_the_timesheet_unit(client, sql, calculated):  # noqa: F811
    """Офис со строкой табеля на NS2 и набором «вся сеть» — делится на всех.

    На NS2 ложится только его доля, а не вся зарплата: табель говорит, где
    человек отмечен, а набор — чьи это затраты.
    """
    who = somebody_on(sql, "NS2")
    put_on_network(sql, who)
    others, own = costs_without(sql, who)
    assert own > 0, "у выбранного человека нет стоимости — проверять нечего"

    login_as(client, "director")
    approve(client, calculated)

    waiting = sql.execute(
        """select count(*) from facts where dedup_key like 'payrun:%%'
              and superseded_at is null and allocation = 'pending'"""
    ).fetchone()[0]
    assert waiting == 0, f"ФОТ «вся сеть» висит неразнесённым: {waiting} строк"
    units = [code for (code,) in sql.execute("select code from units").fetchall()]
    assert_lands(
        payroll_by_unit(sql),
        {code: others.get(code, Decimal("0")) + own / len(units) for code in units},
    )


def test_the_set_of_units_beats_the_timesheet_unit(client, sql, calculated):  # noqa: F811
    """Набор {NS1, NS2}, строка табеля на NS2 — делится между NS1 и NS2 поровну."""
    who = somebody_on(sql, "NS2")
    put_on_units(sql, who, ["NS1", "NS2"])
    others, own = costs_without(sql, who)

    login_as(client, "director")
    approve(client, calculated)

    assert_lands(payroll_by_unit(sql), {
        "NS1": others.get("NS1", Decimal("0")) + own / 2,
        "NS2": others.get("NS2", Decimal("0")) + own / 2,
        "BG1": others.get("BG1", Decimal("0")),
    })


def test_one_unit_in_the_set_takes_it_all(client, sql, calculated):  # noqa: F811
    """Набор {NS1}, строка табеля на NS2 — целиком на NS1, на NS2 ничего."""
    who = somebody_on(sql, "NS2")
    put_on_units(sql, who, ["NS1"])
    others, own = costs_without(sql, who)

    login_as(client, "director")
    approve(client, calculated)

    assert_lands(payroll_by_unit(sql), {
        "NS1": others.get("NS1", Decimal("0")) + own,
        "NS2": others.get("NS2", Decimal("0")),
    })


def test_no_set_keeps_the_timesheet_unit(client, sql, calculated):  # noqa: F811
    """Набор не задан вовсе (люди до T221) — старое поведение: точка строки."""
    who = somebody_on(sql, "NS2")
    others, own = costs_without(sql, who)

    login_as(client, "director")
    approve(client, calculated)

    assert_lands(payroll_by_unit(sql), {
        "NS2": others.get("NS2", Decimal("0")) + own,
    })


# --- проводку делает полный доступ, и отсутствие правила не молчит ------------


def payrun_facts(sql) -> int:  # noqa: F811
    return sql.execute(
        "select count(*) from facts where dedup_key like 'payrun:%%' and superseded_at is null"
    ).fetchone()[0]


def test_a_role_with_a_partial_view_cannot_post_the_month(client, sql, calculated):  # noqa: F811
    """Утверждает урезанная роль — отказ словами, а не проводка по её срезу.

    Проводка читает ведомости и привязки под политиками того, кто утверждает.
    Управляющий NS1 видит только свои строки и свои точки: человек на двух
    точках ушёл бы целиком на одну, сетевой — на точку табеля, а ведомости
    соседних точек не попали бы в P&L вовсе. И никто бы этого не увидел.
    """
    who = somebody_on(sql, "NS1")
    put_on_units(sql, who, ["NS1", "NS2"])
    granted = sql.execute(
        """update roles set permissions = permissions || '["period.approve"]'::jsonb
            where code = 'manager' and not permissions ? 'period.approve'
        returning id"""
    ).fetchall()
    try:
        login_as(client, "manager")
        page = body(approve(client, calculated))
    finally:
        for (role_id,) in granted:
            sql.execute(
                "update roles set permissions = permissions - 'period.approve' where id = %s",
                (role_id,),
            )
        client.post("/logout/")

    assert payrun_facts(sql) == 0, "урезанная роль провела месяц по своему срезу"
    assert "все точки" in page, f"отказ без объяснения: {page[:600]}"


def test_a_missing_network_rule_stops_the_approval_loudly(client, sql, calculated):  # noqa: F811
    """Нет общего правила «поровну» — утверждение отказывает, а не оставляет `pending`.

    Правило поставляет продукт (`0270`), но строка P&L может появиться путём,
    который правила не заводит. Тогда ФОТ офиса повис бы неразнесённым молча, и
    узналось бы это через месяц по дыре в P&L точек.
    """
    who = somebody_on(sql, "NS2")
    put_on_network(sql, who)
    saved = sql.execute(
        """delete from allocation_rules where tenant_id is null
        returning id, pnl_item_id, method, ledger, valid_from, valid_to"""
    ).fetchall()
    try:
        login_as(client, "director")
        page = body(approve(client, calculated))
    finally:
        for row in saved:
            sql.execute(
                """insert into allocation_rules
                       (id, tenant_id, pnl_item_id, method, ledger, valid_from, valid_to)
                   values (%s, null, %s, %s, %s, %s, %s)""",
                row,
            )
        client.post("/logout/")

    assert saved, "общих правил не было и до теста — проверять нечего"
    assert payrun_facts(sql) == 0, "месяц утверждён с ФОТ, висящим без разнесения"
    assert "правил" in page, f"отказ без объяснения: {page[:600]}"
