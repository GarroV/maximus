"""Аналитика по кадрам: динамика по месяцам, разрезы и отказ считать от малой базы (T167).

Первый отчёт продукта, который смотрит сквозь время, — и вопросы у него свои.
Здесь проверяется то, в чём ошибка тихая: арифметика показателей и отказ
показывать процент, посчитанный от горстки людей.

**Почему отказ считать проверяется наравне с расчётом.** Эталон говорит прямо:
«по семи курьерам нельзя судить о текучести курьеров». Процент, посчитанный от
семи человек, — это не приблизительное знание, а его видимость: партнёр читает
«28,6%» и увольняет управляющего из-за двух совпадений. Поэтому «мало данных» —
такой же результат работы отчёта, как число, и он проверяется так же.

Сборка отделена от базы намеренно (`assemble` не знает о ней ничего), поэтому эти
проверки идут без Postgres и быстро. Сходимость с ведомостью — отдельная проверка
ниже, и она уже требует базы.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from reports import people

JAN, FEB, MAR = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)


def fact(period, who, unit="BG1", group="kitchen", payroll="0", hours="0"):
    return people.Fact(
        period=period, employee_id=who,
        unit_code=unit, unit_title=f"Точка {unit}",
        group_code=group, group_title=f"Группа {group}",
        payroll=Decimal(payroll), hours=Decimal(hours),
    )


def move(who, *, unit="BG1", group="kitchen", hired=None, left=None, tenure=None):
    return people.Move(
        employee_id=who, name=f"Фамилия {who}",
        unit_code=unit, unit_title=f"Точка {unit}",
        group_code=group, group_title=f"Группа {group}",
        hired_at=hired, dismissed_at=left, tenure_months=tenure,
    )


def crowd(period, size, *, unit="BG1", group="kitchen", payroll="100", hours="160"):
    """Толпа, на которой база заведомо достаточна для процента."""
    return [fact(period, f"{unit}-{n}", unit, group, payroll, hours) for n in range(size)]


class TestДинамикаПоМесяцам:
    """Столбики графика — то, ради чего отчёт вообще существует."""

    def test_месяц_складывает_всех_людей_и_все_часы(self):
        report = people.assemble(
            [fact(JAN, "a", payroll="100", hours="160"),
             fact(JAN, "b", payroll="50", hours="80")],
            [],
        )
        assert [month.period for month in report.months] == [JAN]
        assert report.months[0].payroll == Decimal("150")
        assert report.months[0].hours == Decimal("240")
        assert report.months[0].heads == 2

    def test_месяцы_идут_по_возрастанию(self):
        """Иначе график читается справа налево — и никто этого не заметит."""
        report = people.assemble(
            [fact(MAR, "a"), fact(JAN, "a"), fact(FEB, "a")], [],
        )
        assert [month.period for month in report.months] == [JAN, FEB, MAR]

    def test_численность_считается_по_людям_а_не_по_строкам(self):
        """У человека бывает две строки табеля за месяц — перевод внутри месяца."""
        report = people.assemble([fact(JAN, "a"), fact(JAN, "a")], [])
        assert report.months[0].heads == 1

    def test_стоимость_часа_это_фот_делённый_на_часы(self):
        report = people.assemble([fact(JAN, "a", payroll="320", hours="160")], [])
        assert report.months[0].hour_cost == Decimal("2")

    def test_без_часов_стоимость_часа_не_выдумывается(self):
        """Ноль часов — не ноль стоимости, а отсутствие ответа."""
        report = people.assemble([fact(JAN, "a", payroll="320", hours="0")], [])
        assert report.months[0].hour_cost is None


class TestДоляФотОтВыручки:
    """Выручки в продукте нет; отчёт обязан молчать об этом словами, а не нулём."""

    def test_без_выручки_доли_нет(self):
        report = people.assemble([fact(JAN, "a", payroll="100")], [])
        assert report.months[0].share is None
        assert report.revenue_known is False

    def test_с_выручкой_доля_считается(self):
        """Запрос к фактам написан заранее: приедет коннектор — экран оживёт сам."""
        report = people.assemble(
            [fact(JAN, "a", payroll="270")], [], revenue={JAN: Decimal("1000")},
        )
        assert report.months[0].share == Decimal("27")
        assert report.revenue_known is True

    def test_нулевая_выручка_не_делится(self):
        """Ноль в знаменателе — не повод показать бесконечность или упасть."""
        report = people.assemble(
            [fact(JAN, "a", payroll="100")], [], revenue={JAN: Decimal("0")},
        )
        assert report.months[0].share is None


class TestРазрезы:
    """Точка и группа — два разреза одних и тех же строк, а не две выборки."""

    def test_точки_разделяют_деньги_и_людей(self):
        report = people.assemble(
            [fact(JAN, "a", unit="BG1", payroll="100"),
             fact(JAN, "b", unit="NS1", payroll="40")],
            [],
        )
        by_code = {row.code: row for row in report.by_unit}
        assert by_code["BG1"].payroll == Decimal("100")
        assert by_code["NS1"].payroll == Decimal("40")
        assert by_code["BG1"].heads == 1

    def test_группы_разделяют_то_же_самое(self):
        report = people.assemble(
            [fact(JAN, "a", group="kitchen", payroll="100"),
             fact(JAN, "b", group="courier", payroll="40")],
            [],
        )
        by_code = {row.code: row for row in report.by_group}
        assert by_code["kitchen"].payroll == Decimal("100")
        assert by_code["courier"].payroll == Decimal("40")

    def test_сумма_разреза_равна_итогу(self):
        """Разрез, не складывающийся в итог, — это две правды об одних деньгах."""
        report = people.assemble(
            [fact(JAN, "a", unit="BG1", payroll="100"),
             fact(JAN, "b", unit="NS1", payroll="40"),
             fact(FEB, "a", unit="BG1", payroll="110")],
            [],
        )
        assert sum(row.payroll for row in report.by_unit) == report.payroll
        assert sum(row.payroll for row in report.by_group) == report.payroll

    def test_человек_без_точки_попадает_в_свою_строку(self):
        """Офис работает на всю сеть; молча приписать его пиццерии нельзя."""
        report = people.assemble([fact(JAN, "a", unit="", payroll="90")], [])
        assert [row.code for row in report.by_unit] == [""]
        assert report.by_unit[0].payroll == Decimal("90")

    def test_фильтр_по_точке_убирает_чужие_деньги(self):
        report = people.assemble(
            [fact(JAN, "a", unit="BG1", payroll="100"),
             fact(JAN, "b", unit="NS1", payroll="40")],
            [], unit_filter="BG1",
        )
        assert report.payroll == Decimal("100")
        assert report.months[0].heads == 1

    def test_фильтр_по_точке_убирает_и_чужие_уходы(self):
        """Иначе на экране одной точки текучесть считается по чужим людям."""
        report = people.assemble(
            crowd(JAN, 14, unit="BG1"),
            [move("x", unit="NS1", left=FEB), move("y", unit="BG1", left=FEB)],
            unit_filter="BG1",
        )
        assert report.left == 1


class TestТекучесть:
    """Как считается уход — и когда он не считается вовсе."""

    def test_текучесть_это_ушедшие_к_средней_численности(self):
        """Определение из эталона: ушедшие за период к среднему числу людей за него."""
        facts = crowd(JAN, 20) + crowd(FEB, 20)
        report = people.assemble(facts, [move("a", left=FEB), move("b", left=FEB)])
        assert report.base == Decimal("20")
        assert report.turnover == Decimal("10")

    def test_численность_усредняется_а_не_берётся_последняя(self):
        """Точка, где половина людей появилась в конце, не должна выглядеть стабильной."""
        facts = crowd(JAN, 10) + crowd(FEB, 30)
        report = people.assemble(facts, [move("a", left=FEB)])
        assert report.base == Decimal("20")

    def test_мало_данных_процента_нет(self):
        """Семь курьеров — не статистика, и отчёт об этом говорит, а не считает."""
        report = people.assemble(crowd(JAN, 7), [move("a", left=JAN)])
        assert report.base < people.MIN_BASE
        assert report.turnover is None

    def test_на_пороге_базы_процент_уже_считается(self):
        """Граница названа числом, а не «около десятка», и проверяется ровно на ней."""
        facts = crowd(JAN, people.MIN_BASE)
        report = people.assemble(facts, [move("a", left=JAN)])
        assert report.turnover is not None

    def test_разрез_отказывает_отдельно_от_итога(self):
        """У сети база достаточна, у маленькой точки — нет: процент только там, где можно."""
        facts = crowd(JAN, 20, unit="BG1") + crowd(JAN, 4, unit="NS1")
        report = people.assemble(
            facts, [move("a", unit="BG1", left=JAN), move("b", unit="NS1", left=JAN)],
        )
        by_code = {row.code: row for row in report.by_unit}
        assert by_code["BG1"].turnover is not None
        assert by_code["NS1"].turnover is None
        assert report.turnover is not None

    def test_без_уходов_текучесть_ноль_а_не_прочерк(self):
        """Ноль уходов — это знание, и его прячут только вместе с малой базой."""
        report = people.assemble(crowd(JAN, 20), [])
        assert report.turnover == Decimal("0")


class TestУшедшие:
    """Шесть человек ещё можно назвать по именам — эталон требует именно этого."""

    def test_ушедшие_идут_по_дате_ухода(self):
        report = people.assemble(
            crowd(JAN, 14),
            [move("b", left=MAR, tenure=3), move("a", left=JAN, tenure=9)],
        )
        assert [row.dismissed_at for row in report.leavers] == [JAN, MAR]

    def test_принятый_в_список_ушедших_не_попадает(self):
        report = people.assemble(crowd(JAN, 14), [move("a", hired=JAN)])
        assert report.leavers == []
        assert report.hired == 1
        assert report.left == 0

    def test_средний_срок_считается_по_известным(self):
        report = people.assemble(
            crowd(JAN, 14),
            [move("a", left=JAN, tenure=4), move("b", left=FEB, tenure=8)],
        )
        assert report.avg_tenure == Decimal("6")

    def test_без_ушедших_среднего_срока_нет(self):
        report = people.assemble(crowd(JAN, 14), [])
        assert report.avg_tenure is None

    @pytest.mark.parametrize(
        ("hired", "left", "months"),
        [
            (date(2025, 6, 10), date(2026, 3, 5), 9),
            (date(2026, 1, 20), date(2026, 1, 25), 0),
            (None, date(2026, 3, 1), None),
            (date(2026, 1, 1), None, None),
        ],
    )
    def test_срок_работы_считается_в_месяцах(self, hired, left, months):
        """Неизвестна одна из дат — срок не выдумывается, а отсутствует."""
        assert people._tenure(hired, left) == months


class TestПереработки:
    """Сверх нормы — про часы. Денег по непроверенному коэффициенту здесь нет."""

    def test_сверх_нормы_это_разница_с_нормой_человека(self):
        row = people.OverNorm(name="Иванов", unit_title="BG1",
                              hours=Decimal("196"), norm=Decimal("176"),
                              payroll=Decimal("1000"))
        assert row.over == Decimal("20")

    def test_норма_у_каждого_своя(self):
        """Полставки — не переработка; общая норма записала бы её в переработку."""
        half = people.OverNorm(name="Петров", unit_title="BG1",
                               hours=Decimal("100"), norm=Decimal("88"),
                               payroll=Decimal("500"))
        assert half.over == Decimal("12")

    def test_список_отсортирован_по_превышению(self):
        report = people.assemble([fact(JAN, "a")], [], over_norm=[
            people.OverNorm("Малый", "BG1", Decimal("180"), Decimal("176"), Decimal("1")),
            people.OverNorm("Большой", "BG1", Decimal("200"), Decimal("176"), Decimal("1")),
        ])
        assert [row.name for row in report.over_norm] == ["Большой", "Малый"]
        assert report.over_hours == Decimal("28")


class TestПустота:
    """Пустой месяц не должен падать: у нового партнёра данных нет вовсе."""

    def test_отчёт_без_единой_строки_собирается(self):
        report = people.assemble([], [])
        assert report.months == []
        assert report.payroll == Decimal("0")
        assert report.heads == 0
        assert report.turnover is None
        assert report.avg_tenure is None

    def test_месяцы_берутся_последние(self):
        months = [date(2026, month, 1) for month in range(1, 9)]
        assert people.months_of(months, 6) == months[-6:]
        assert people.months_of(months[:2], 6) == months[:2]
