"""Консолидация сети: месяц всех видимых партнёров в одной валюте (T209, модуль 5).

Эталон: «четыре партнёра, три страны · единая валюта EUR по курсу на последний
день периода». Партнёр здесь — тенант; сеть — все партнёры, в которых состоит
смотрящий. Кого он видит, решает база (политика `tenants` и `security_invoker`
у `pnl_lines`), а не этот модуль (D014): второго ответа на вопрос о доступе тут
нет.

**Валюта консолидации — настройка партнёра** (`tenants.report_currency`), не
константа. Сеть складывается в валюте, общей для большинства её партнёров;
партнёр с другой валютой показан строкой, но в итог сети не входит — и это
сказано словами, а не спрятано.

**Пересчёт делает база** (`pnl_lines.amount_report`, миграция `0277`): у
закрытого месяца — по курсу, закреплённому при закрытии, у открытого — по
действующему на конец месяца. Здесь только сложение по партнёру.

**Чего не знаем — не складываем.** Партнёр, у которого хоть одна сумма без
курса или скрыты строки зарплаты, в итог сети не входит: сумма без части
фактов выглядела бы полной, не будучи ею. Незакрытые месяцы в итог входят, но
подвал называет их числом — эталон: «складывать закрытое с незакрытым и не
говорить об этом — единственный способ получить цифру, которой никто не
поверит».
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from payrun.posting import LABOUR, TAXES
from reports.pnl import UNCLASSIFIED

ZERO = Decimal("0")


@dataclass(frozen=True)
class Rate:
    """Строка справочника курсов, которой посчитан партнёр."""

    base: str
    quote: str
    value: Decimal
    on: date
    note: str
    fixed: bool          # закреплён за закрытым месяцем


@dataclass(frozen=True)
class Partner:
    """Строка консолидации: один партнёр за месяц в валюте консолидации."""

    tenant_id: str
    title: str
    country_code: str
    currency: str            # валюта учёта
    report_currency: str     # валюта консолидации партнёра
    units: int
    revenue: Decimal
    payroll: Decimal         # положительное число: сколько ушло на ФОТ и взносы
    profit: Decimal
    unrated: int             # фактов без курса
    payroll_hidden: bool
    status: str | None       # open | closed | None — месяц не заведён
    rate: Rate | None

    @property
    def complete(self) -> bool:
        """Сумма партнёра полная: у каждого факта есть курс и ничего не скрыто."""
        return self.unrated == 0 and not self.payroll_hidden

    @property
    def closed(self) -> bool:
        return self.status == "closed"


@dataclass(frozen=True)
class Network:
    """Сеть за месяц: строки партнёров и итог тех, кого можно сложить."""

    period: date
    currency: str | None
    partners: list = field(default_factory=list)

    @property
    def summed(self) -> list:
        """Кто входит в итог: полная сумма в валюте сети."""
        return [p for p in self.partners
                if p.complete and p.report_currency == self.currency]

    @property
    def left_out(self) -> list:
        return [p for p in self.partners if p not in self.summed]

    @property
    def revenue(self) -> Decimal:
        return sum((p.revenue for p in self.summed), ZERO)

    @property
    def payroll(self) -> Decimal:
        return sum((p.payroll for p in self.summed), ZERO)

    @property
    def profit(self) -> Decimal:
        return sum((p.profit for p in self.summed), ZERO)

    @property
    def units(self) -> int:
        return sum(p.units for p in self.summed)

    @property
    def not_closed(self) -> int:
        """Сколько партнёров в итоге с незакрытым месяцем."""
        return sum(1 for p in self.summed if not p.closed)


def share(part: Decimal, whole: Decimal) -> Decimal | None:
    """Доля в процентах; без выручки доли нет, а не ноль и не деление на ноль."""
    if not whole:
        return None
    return (part / whole * 100).quantize(Decimal("0.1"))


SQL = """
select t.id::text, t.title, t.country_code, t.base_currency, t.report_currency,
       (select count(*) from units u
         where u.tenant_id = t.id
           and (u.opened_at is null or u.opened_at <= %(end)s)
           and (u.closed_at is null or u.closed_at >= %(period)s)) as units,
       coalesce(s.revenue, 0), coalesce(s.payroll, 0), coalesce(s.profit, 0),
       coalesce(s.unrated, 0),
       not app_sees_payroll(t.id) as payroll_hidden,
       p.status::text,
       r.base_currency, r.quote_currency, r.rate, r.rate_date, r.note,
       x.id is not null as fixed
  from tenants t
  left join periods p on p.tenant_id = t.id and p.period = %(period)s
  left join lateral (
      select sum(l.amount_report) filter (where l.kind = 'revenue')               as revenue,
             -sum(l.amount_report) filter (where l.pnl_code in (%(labour)s, %(taxes)s)) as payroll,
             sum(l.amount_report) filter (where l.pnl_code <> %(unclassified)s)   as profit,
             count(*) filter (where l.amount_report is null)                       as unrated
        from pnl_lines l
       where l.tenant_id = t.id and l.period = %(period)s and l.kind <> 'transfer'
  ) s on true
  left join fx_rates r
         on r.id = report_rate_row(t.id, %(period)s, t.base_currency, t.report_currency)
  left join period_fx_rates x
         on x.tenant_id = t.id and x.period = %(period)s
        and x.currency = t.base_currency and x.report_currency = t.report_currency
 order by t.title, t.id
"""


def build(period: date) -> Network:
    """Консолидация месяца по всем партнёрам, которых видит смотрящий."""
    from django.db import connection

    with connection.cursor() as cursor:
        return collect(cursor, period)


def collect(cursor, period: date) -> Network:
    """То же по готовому курсору: контекст пользователя уже выставлен."""
    period = period.replace(day=1)
    cursor.execute(SQL, {
        "period": period, "end": _month_end(period), "labour": LABOUR, "taxes": TAXES,
        "unclassified": UNCLASSIFIED,
    })
    partners = [_partner(row) for row in cursor.fetchall()]
    return Network(period=period, currency=_network_currency(partners), partners=partners)


def _partner(row) -> Partner:
    (tenant_id, title, country, currency, report, units, revenue, payroll, profit,
     unrated, payroll_hidden, status, base, quote, value, on, note, fixed) = row
    rate = Rate(base, quote, value, on, note, fixed) if base else None
    return Partner(
        tenant_id=tenant_id, title=title, country_code=country, currency=currency,
        report_currency=report, units=units, revenue=revenue,
        payroll=payroll if not payroll_hidden else ZERO, profit=profit,
        unrated=unrated, payroll_hidden=payroll_hidden, status=status, rate=rate,
    )


def _network_currency(partners: list) -> str | None:
    """Валюта сети — общая для большинства партнёров; при равенстве — по алфавиту."""
    if not partners:
        return None
    counts = Counter(p.report_currency for p in partners)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _month_end(period: date) -> date:
    following = date(period.year + period.month // 12, period.month % 12 + 1, 1)
    return date.fromordinal(following.toordinal() - 1)
