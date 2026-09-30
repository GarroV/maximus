"""Аналитика по кадрам: сколько людей, во сколько они обходятся и кто уходит.

Первый отчёт продукта, который смотрит **сквозь время**. Всё, что было до него, —
срез одного месяца: ведомость, P&L, расхождения, сверка. Поэтому здесь впервые
приходится отвечать на вопросы, которых у среза не возникает: на какую дату
человек относится к точке, что считать уходом, из чего берётся доля.

Решения, на которых держится модуль.

**ФОТ — это сумма видимых компонентов, ровно как в ведомости.** Не `payslip_totals`:
те посчитаны по всем регистрам сразу и видны только роли, которой видны все
регистры строки (политика `ledger_visibility`, T050). Возьми итог оттуда — и у
бухгалтера с неполным доступом отчёт молча разошёлся бы с ведомостью, которую он
видит на соседнем экране. Здесь складывается то же самое, что он там читает
(`payrun/sheet.py`), и сходимость получается по построению, а не проверкой.

**Человек относится к ОДНОЙ точке — той, что стоит в его строке ведомости.**
Это же правило действует для денег, для голов и для уходов: три разных правила на
одном экране дали бы «стоимость часа», не сходящуюся ни с ФОТ, ни с часами. Делить
затраты человека между несколькими точками продукт уже умеет в другом месте
(`payrun/posting.py`, набор точек `employee_units`) — но там отвечают на вопрос
«чьи это затраты» для P&L, а здесь на вопрос «сколько людей и сколько они стоят».
Половину уволившегося показать нельзя. Шов оставлен один: `unit_of`.

**Уход — это `employees.dismissed_at`, а не смена точки.** Эталон требует:
«переводы между точками не считаются уходом». В схеме это выходит само собой —
перевод меняет версию условий найма, а не дату увольнения. Отдельного признака
перевода в базе нет, и выдумывать его здесь не нужно.

**Процент от малой базы не показывается вовсе.** Эталон: «по семи курьерам нельзя
судить о текучести курьеров». Один лишний уход на базе в семь человек двигает
показатель на четырнадцать пунктов, и партнёр увольняет управляющего из-за двух
совпадений. Ниже `MIN_BASE` человек процента нет — стоит слово, а не число.

**Выручки в продукте нет.** Она придёт с коннектором Dodo IS, а до тех пор доля
ФОТ от выручки — прочерк, а не ноль: ноль читался бы как посчитанный ответ. Тот же
приём, что на списке периодов (`web/views._share_of_revenue`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# Ниже этой базы процент не считается. Двенадцать — из эталона (`minBase`): на
# базе меньше дюжины один человек весит больше восьми пунктов, и показатель
# начинает рассказывать про случайность, а не про точку.
MIN_BASE = 12

# Сколько месяцев показывается. Шесть — из эталона («январь — июнь 2026 · шесть
# закрытых месяцев»): столбиков должно хватать, чтобы увидеть направление, и не
# быть столько, чтобы график перестал читаться на 1440.
MONTHS_SHOWN = 6

# Код и название среза «человек не привязан к точке». Такие затраты в продукте
# уже называются «вся сеть» (разнесение расходов), и второго названия одному и
# тому же заводить не нужно.
NETWORK = ""

ZERO = Decimal("0")


@dataclass(frozen=True)
class Fact:
    """Один человек в одном месяце: где работал, в какой группе, сколько стоил.

    Единица, из которой собираются все разрезы. Разрез — это группировка этих
    строк, а не отдельная выборка: две выборки одного и того же расходятся молча
    (тот же довод, что у `payrun/sheet.collect_cells`).
    """

    period: date
    employee_id: str
    unit_code: str
    unit_title: str
    group_code: str
    group_title: str
    payroll: Decimal = ZERO
    hours: Decimal = ZERO


@dataclass(frozen=True)
class Move:
    """Приход или уход одного человека: когда, откуда, из какой группы."""

    employee_id: str
    name: str
    unit_code: str
    unit_title: str
    group_code: str
    group_title: str
    hired_at: date | None
    dismissed_at: date | None
    # Сколько месяцев отработал к моменту ухода. Пусто — человек не уходил.
    tenure_months: int | None = None


@dataclass(frozen=True)
class MonthPoint:
    """Месяц на графике динамики."""

    period: date
    payroll: Decimal
    hours: Decimal
    heads: int
    revenue: Decimal | None = None

    @property
    def hour_cost(self) -> Decimal | None:
        """Стоимость отработанного часа. Нет часов — нет и ответа."""
        return (self.payroll / self.hours) if self.hours else None

    @property
    def per_head(self) -> Decimal | None:
        return (self.payroll / self.heads) if self.heads else None

    @property
    def share(self) -> Decimal | None:
        """Доля ФОТ в выручке. Нет выручки — прочерк, а не ноль."""
        if not self.revenue:
            return None
        return self.payroll / self.revenue * 100


@dataclass(frozen=True)
class Slice:
    """Строка разреза: точка или группа за весь показанный диапазон."""

    code: str
    title: str
    payroll: Decimal
    hours: Decimal
    # Численность последнего показанного месяца: «сейчас столько».
    heads: int
    hired: int
    left: int
    # Средняя численность за диапазон — база, от которой считается текучесть.
    base: Decimal

    @property
    def hour_cost(self) -> Decimal | None:
        return (self.payroll / self.hours) if self.hours else None

    @property
    def enough_base(self) -> bool:
        return self.base >= MIN_BASE

    @property
    def turnover(self) -> Decimal | None:
        """Текучесть: ушедшие за диапазон к средней численности за него.

        Пусто — база мала, и процент не считается вовсе. Это не «нет данных»,
        а отказ отвечать: посчитанный процент от семи человек выглядит как знание.
        """
        if not self.enough_base or not self.base:
            return None
        return Decimal(self.left) / self.base * 100


@dataclass(frozen=True)
class OverNorm:
    """Человек, отработавший больше своей нормы месяца."""

    name: str
    unit_title: str
    hours: Decimal
    norm: Decimal
    payroll: Decimal

    @property
    def over(self) -> Decimal:
        return self.hours - self.norm


@dataclass(frozen=True)
class Analytics:
    """Отчёт целиком."""

    months: list = field(default_factory=list)          # list[MonthPoint]
    by_unit: list = field(default_factory=list)         # list[Slice]
    by_group: list = field(default_factory=list)        # list[Slice]
    leavers: list = field(default_factory=list)         # list[Move]
    over_norm: list = field(default_factory=list)       # list[OverNorm]
    unit_filter: str | None = None

    @property
    def payroll(self) -> Decimal:
        return sum((month.payroll for month in self.months), ZERO)

    @property
    def hours(self) -> Decimal:
        return sum((month.hours for month in self.months), ZERO)

    @property
    def heads(self) -> int:
        """Сколько людей в последнем показанном месяце."""
        return self.months[-1].heads if self.months else 0

    @property
    def hired(self) -> int:
        return sum(row.hired for row in self.by_unit)

    @property
    def left(self) -> int:
        return sum(row.left for row in self.by_unit)

    @property
    def base(self) -> Decimal:
        """Средняя численность за диапазон — база текучести по всей сети."""
        if not self.months:
            return ZERO
        return Decimal(sum(month.heads for month in self.months)) / len(self.months)

    @property
    def enough_base(self) -> bool:
        return self.base >= MIN_BASE

    @property
    def turnover(self) -> Decimal | None:
        if not self.enough_base or not self.base:
            return None
        return Decimal(self.left) / self.base * 100

    @property
    def avg_tenure(self) -> Decimal | None:
        """Средний срок работы ушедших, в месяцах."""
        known = [row.tenure_months for row in self.leavers if row.tenure_months is not None]
        if not known:
            return None
        return Decimal(sum(known)) / len(known)

    @property
    def over_hours(self) -> Decimal:
        return sum((row.over for row in self.over_norm), ZERO)

    @property
    def revenue_known(self) -> bool:
        return any(month.revenue for month in self.months)


# ────────────────────────────── чистая сборка ──────────────────────────────


def assemble(facts, moves, *, revenue=None, over_norm=(), unit_filter=None) -> Analytics:
    """Собрать отчёт из готовых строк. Базы не знает — поэтому и тестируется без неё."""
    revenue = revenue or {}
    kept = [fact for fact in facts if unit_filter in (None, fact.unit_code)]
    periods = sorted({fact.period for fact in kept})

    months = [
        MonthPoint(
            period=period,
            payroll=sum((f.payroll for f in kept if f.period == period), ZERO),
            hours=sum((f.hours for f in kept if f.period == period), ZERO),
            heads=len({f.employee_id for f in kept if f.period == period}),
            revenue=revenue.get(period),
        )
        for period in periods
    ]

    moves_kept = [move for move in moves if unit_filter in (None, move.unit_code)]
    return Analytics(
        months=months,
        by_unit=_slices(kept, moves_kept, periods, "unit"),
        by_group=_slices(kept, moves_kept, periods, "group"),
        leavers=sorted(
            (move for move in moves_kept if move.dismissed_at),
            key=lambda move: move.dismissed_at,
        ),
        over_norm=sorted(over_norm, key=lambda row: row.over, reverse=True),
        unit_filter=unit_filter,
    )


def _slices(facts, moves, periods, dimension: str) -> list:
    """Разрез по точке или по группе. Обе таблицы устроены одинаково — и это намеренно.

    Партнёр читает их подряд; разная форма заставляла бы перечитывать шапку.
    """
    code = (lambda row: row.unit_code) if dimension == "unit" else (lambda row: row.group_code)
    title = (lambda row: row.unit_title) if dimension == "unit" else (lambda row: row.group_title)

    keys = {}
    for fact in facts:
        keys.setdefault(code(fact), title(fact))
    for move in moves:
        keys.setdefault(code(move), title(move))

    last = periods[-1] if periods else None
    rows = []
    for key, name in keys.items():
        mine = [fact for fact in facts if code(fact) == key]
        heads_by_month = [
            len({f.employee_id for f in mine if f.period == period}) for period in periods
        ]
        rows.append(Slice(
            code=key,
            title=name,
            payroll=sum((f.payroll for f in mine), ZERO),
            hours=sum((f.hours for f in mine), ZERO),
            heads=len({f.employee_id for f in mine if f.period == last}) if last else 0,
            hired=sum(1 for move in moves if code(move) == key and move.hired_at),
            left=sum(1 for move in moves if code(move) == key and move.dismissed_at),
            base=(Decimal(sum(heads_by_month)) / len(periods)) if periods else ZERO,
        ))

    # Сначала те, где кто-то работает: строка «ушли все» не должна открывать
    # таблицу только потому, что её код начинается с буквы раньше.
    return sorted(rows, key=lambda row: (-row.payroll, row.title))


def months_of(periods, count: int = MONTHS_SHOWN) -> list:
    """Последние `count` месяцев из имеющихся, в порядке чтения — слева направо."""
    return sorted(periods)[-count:]


# ─────────────────────────────── выборки из базы ───────────────────────────────


def collect(tenant_id, months) -> list:
    """Строки «человек × месяц» из базы. Фильтра по регистру нет — его ставит база.

    Две выборки (деньги и часы) сводятся в одну строку здесь, а не в SQL: часы
    живут в табеле и есть у людей, которым ещё не считали зарплату, а компоненты
    расчёта — наоборот. Внешним соединением одного к другому получилась бы строка
    с чужой точкой.
    """
    if not months:
        return []
    from django.db import connection

    args = {"tenant": str(tenant_id), "since": months[0], "until": months[-1]}
    rows: dict = {}

    with connection.cursor() as cursor:
        # Деньги. `pay_components` — то же, из чего собрана ведомость: политика
        # видимости регистров стоит на них, и срез роли делает база (D014).
        cursor.execute(
            """
            select r.period, s.employee_id::text,
                   coalesce(u.code, ''), coalesce(u.title, ''),
                   coalesce(g.code, ''), coalesce(g.title, ''),
                   sum(c.amount)
              from pay_components c
              join payslips s on s.id = c.payslip_id
              join payruns r on r.id = s.payrun_id
              left join units u on u.id = s.unit_id
              left join lateral (
                  select t.group_id
                    from employment_terms t
                   where t.employee_id = s.employee_id
                     and t.valid_from <= (r.period + interval '1 month' - interval '1 day')
                     and (t.valid_to is null or t.valid_to >= r.period)
                   order by t.valid_from desc
                   limit 1
              ) term on true
              left join employee_groups g on g.id = term.group_id
             where c.tenant_id = %(tenant)s
               and r.period between %(since)s and %(until)s
             group by r.period, s.employee_id, u.code, u.title, g.code, g.title
            """,
            args,
        )
        for period, employee, unit_code, unit_title, group_code, group_title, amount in cursor:
            key = (period, employee)
            rows[key] = Fact(period, employee, unit_code, unit_title,
                             group_code, group_title, amount or ZERO, ZERO)

        # Часы. Точка берётся из табеля: перевод внутри месяца даёт две строки
        # табеля с разными точками, и складывать их в одну нельзя — часы уйдут
        # не на ту пиццерию. Но человек в отчёте один, поэтому строка «человек ×
        # месяц» достаётся точке, где он числится в ведомости.
        cursor.execute(
            """
            select t.period, t.employee_id::text,
                   coalesce(u.code, ''), coalesce(u.title, ''),
                   coalesce(g.code, ''), coalesce(g.title, ''),
                   sum((select coalesce(sum(value::numeric), 0)
                          from jsonb_each_text(t.hours) as h(key, value)))
              from timesheets t
              left join units u on u.id = t.unit_id
              left join lateral (
                  select e.group_id
                    from employment_terms e
                   where e.employee_id = t.employee_id
                     and e.valid_from <= (t.period + interval '1 month' - interval '1 day')
                     and (e.valid_to is null or e.valid_to >= t.period)
                   order by e.valid_from desc
                   limit 1
              ) term on true
              left join employee_groups g on g.id = term.group_id
             where t.tenant_id = %(tenant)s
               and t.period between %(since)s and %(until)s
             group by t.period, t.employee_id, u.code, u.title, g.code, g.title
            """,
            args,
        )
        for period, employee, unit_code, unit_title, group_code, group_title, worked in cursor:
            key = (period, employee)
            known = rows.get(key)
            if known is None:
                # Табель есть, расчёта нет: человек в численности участвует, в
                # деньгах — нет. Молчать о нём значило бы занизить численность.
                rows[key] = Fact(period, employee, unit_code, unit_title,
                                 group_code, group_title, ZERO, worked or ZERO)
                continue
            rows[key] = Fact(known.period, known.employee_id, known.unit_code,
                             known.unit_title, known.group_code, known.group_title,
                             known.payroll, worked or ZERO)

    return [rows[key] for key in sorted(rows, key=lambda key: (key[0], key[1]))]


def movement(tenant_id, months) -> list:
    """Кто пришёл и кто ушёл за показанный диапазон.

    Точка и группа берутся из условий найма, действовавших в момент события: у
    ушедшего в марте спрашивать сегодняшние условия бессмысленно, а у принятого —
    тем более, их ещё нет.
    """
    if not months:
        return []
    from django.db import connection

    last_day = "(%(until)s::date + interval '1 month' - interval '1 day')::date"
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            select e.id::text, e.last_name, e.first_name, e.hired_at, e.dismissed_at,
                   coalesce(u.code, ''), coalesce(u.title, ''),
                   coalesce(g.code, ''), coalesce(g.title, '')
              from employees e
              left join lateral (
                  select t.unit_id, t.group_id
                    from employment_terms t
                   where t.employee_id = e.id
                   order by (t.valid_from <= coalesce(e.dismissed_at, e.hired_at)) desc,
                            t.valid_from desc
                   limit 1
              ) term on true
              left join units u on u.id = term.unit_id
              left join employee_groups g on g.id = term.group_id
             where e.tenant_id = %(tenant)s
               and (
                     (e.hired_at     between %(since)s and {last_day})
                  or (e.dismissed_at between %(since)s and {last_day})
               )
            """,
            {"tenant": str(tenant_id), "since": months[0], "until": months[-1]},
        )
        return [
            Move(
                employee_id=employee_id,
                name=f"{last_name} {first_name}".strip(),
                unit_code=unit_code,
                unit_title=unit_title,
                group_code=group_code,
                group_title=group_title,
                hired_at=hired_at if hired_at and hired_at >= months[0] else None,
                dismissed_at=dismissed_at,
                tenure_months=_tenure(hired_at, dismissed_at),
            )
            for (employee_id, last_name, first_name, hired_at, dismissed_at,
                 unit_code, unit_title, group_code, group_title) in cursor
        ]


def _tenure(hired_at, dismissed_at) -> int | None:
    """Сколько полных месяцев человек отработал. Не знаем даты найма — не знаем срока."""
    if not hired_at or not dismissed_at:
        return None
    months = (dismissed_at.year - hired_at.year) * 12 + (dismissed_at.month - hired_at.month)
    return max(months, 0)


def revenue_by_period(tenant_id, months) -> dict:
    """Выручка по месяцам. Её в продукте пока нет вовсе — словарь будет пустым.

    Запрос написан заранее и работает: выручка приедет с коннектором Dodo IS и
    попадёт в те же факты, что и всё остальное. До тех пор экран показывает
    прочерк и говорит, почему, — а не ноль.
    """
    if not months:
        return {}
    from django.db.models import Sum

    from core.models import Fact as FactRow

    return {
        row["period"]: row["total"]
        for row in FactRow.objects
        .filter(tenant_id=tenant_id, pnl_item__kind="revenue",
                superseded_at__isnull=True,
                period__gte=months[0], period__lte=months[-1])
        .exclude(allocation="split")
        .values("period")
        .annotate(total=Sum("amount"))
        if row["total"]
    }


def over_norm_of(tenant_id, period: date) -> list:
    """Кто отработал больше своей нормы в этом месяце.

    Норма берётся у самого табеля (`timesheets.norm_hours`), а не из календаря
    страны: у человека на полставки она своя, и мерить его общей нормой значило
    бы записать в переработку обычный рабочий месяц.

    Денег здесь нет намеренно. Эталон считает стоимость переработки по
    коэффициенту 1,5, но в правилах страны коэффициент другой (1,26) и помечен
    `status: unverified` — вопрос к бухгалтеру ещё открыт. Число, посчитанное по
    непроверенному коэффициенту, выглядит как знание, поэтому здесь показано
    начисление человека за месяц — то самое, что стоит в ведомости.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """
            select e.last_name, e.first_name, coalesce(u.title, ''),
                   t.worked, t.norm_hours,
                   coalesce((select sum(c.amount)
                               from pay_components c
                               join payslips s on s.id = c.payslip_id
                               join payruns r on r.id = s.payrun_id
                              where s.employee_id = t.employee_id
                                and r.period = t.period), 0)
              from (
                  select ts.employee_id, ts.period, ts.unit_id, ts.norm_hours,
                         (select coalesce(sum(value::numeric), 0)
                            from jsonb_each_text(ts.hours) as h(key, value)) as worked
                    from timesheets ts
                   where ts.tenant_id = %(tenant)s and ts.period = %(period)s
              ) t
              join employees e on e.id = t.employee_id
              left join units u on u.id = t.unit_id
             where t.worked > t.norm_hours
             order by (t.worked - t.norm_hours) desc
            """,
            {"tenant": str(tenant_id), "period": period},
        )
        return [
            OverNorm(name=f"{last} {first}".strip(), unit_title=unit,
                     hours=worked, norm=norm, payroll=payroll)
            for last, first, unit, worked, norm, payroll in cursor
        ]


def known_months(tenant_id, count: int = MONTHS_SHOWN) -> list:
    """Месяцы, о которых вообще есть что сказать: есть расчёт или есть табель."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """
            select period from payruns where tenant_id = %(tenant)s
            union
            select period from timesheets where tenant_id = %(tenant)s
             order by 1 desc limit %(count)s
            """,
            {"tenant": str(tenant_id), "count": count},
        )
        return sorted(row[0] for row in cursor)


def build(tenant_id, *, count: int = MONTHS_SHOWN, unit_filter=None) -> Analytics:
    """Отчёт за последние `count` месяцев. Всё, чего роль не видит, сюда не приезжает."""
    months = known_months(tenant_id, count)
    facts = collect(tenant_id, months)
    return assemble(
        facts,
        movement(tenant_id, months),
        revenue=revenue_by_period(tenant_id, months),
        over_norm=over_norm_of(tenant_id, months[-1]) if months else (),
        unit_filter=unit_filter,
    )
