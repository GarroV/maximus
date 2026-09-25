"""Экран «Аналитика по людям»: стоимость труда, кто уходит, часы (T167, модуль 12).

Первый экран продукта, который смотрит сквозь время. Всё остальное — срез месяца,
и открывают его изнутри периода; этот стоит отдельным пунктом отчётов, потому что
отвечает на вопрос не про месяц, а про полгода.

Считает `reports/people.py`, здесь только слова и адреса — та же граница, что у
ведомости (T028) и у P&L. Практическое следствие: числа на экране приходят из той
же выборки, что и ведомость, и сходятся с ней по построению, а не проверкой.

**Список точек собирается из показанных строк, а не из справочника.** То же
правило, что у разреза ведомости по регистрам (T028, D023): кнопка «Novi Sad»,
ведущая в пустую таблицу, — это сообщение о том, что такая точка есть и на ней
кто-то работает. Подобранный в адресе код чужой точки молча приравнивается к
«все»: отказ и пустая таблица одинаково отвечают на вопрос, которого человеку
задавать не давали.

**Вкладки — обычные ссылки.** Состояние экрана целиком лежит в адресе, поэтому
его можно прислать другому человеку, и оно переживает перезагрузку. JS ради
переключения трёх таблиц здесь не нужен.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from reports import people

from .format import EMPTY, hours, money, separators
from .i18n import month_title
from .principal import get_current_principal

__all__ = ["analytics"]

# Вкладки эталона в его же порядке: сначала деньги, потом люди, потом часы.
TABS = (
    ("cost", gettext_noop("Стоимость труда")),
    ("churn", gettext_noop("Кто уходит")),
    ("hours", gettext_noop("Часы и переработки")),
)

# Самый большой месяц занимает всю полосу. Мерится он по показанным месяцам, а
# не по абсолютной шкале: у партнёра ФОТ месяцев отличается на проценты, и
# полоса «от нуля до сколько бывает вообще» не показала бы ничего (тот же
# приём, что на истории человека, T166).
TOP_BAR = 100

ALL_UNITS = ""


def _share(value) -> str:
    """Доля в процентах без знака: «27,2 %». Нет значения — прочерк, не ноль.

    Отдельно от `format.percent`: тот печатает ОТКЛОНЕНИЕ и ставит «+» у роста.
    У доли плюс означал бы, что она выросла, — а она просто такая.
    """
    if value is None:
        return EMPTY
    _thousands, decimal = separators()
    return f"{Decimal(value).quantize(Decimal('0.1'))}".replace(".", decimal) + " %"


def _people(count) -> str:
    """Численность словами языка страницы: «14 чел.»"""
    return _("%(count)s чел.") % {"count": count}


def _unit_title(code: str, title: str) -> str:
    """Название точки. Пусто — человек не привязан к пиццерии, значит вся сеть."""
    return title or _("Вся сеть")


def _group_title(code: str, title: str) -> str:
    return title or _("Без группы")


@login_required
def analytics(request):
    """Аналитика по кадрам за последние месяцы (модуль 12 эталона).

    Права у экрана своего нет и не заводится: он ничего не пишет, а видит ровно
    то, что база отдаёт роли открывшего (D014). Управляющий точки увидит здесь
    свою точку — и не потому, что мы отфильтровали, а потому что политики базы
    больше ему не дали.
    """
    who = get_current_principal(request)
    tenant_id = who.tenant_id if who else None

    asked = request.GET.get("unit", ALL_UNITS)
    tab = request.GET.get("tab", TABS[0][0])
    if tab not in dict(TABS):
        tab = TABS[0][0]

    # Полный отчёт собирается один раз без фильтра: из него берётся и список
    # точек (только тех, где кто-то показан), и проверка, что спрошенная точка
    # вообще существует для этой роли.
    whole = people.build(tenant_id)
    known = {row.code for row in whole.by_unit}
    chosen = asked if asked in known else ALL_UNITS
    report = whole if chosen == ALL_UNITS else people.build(tenant_id, unit_filter=chosen)

    return render(request, "web/reports/people.html", {
        "tab": tab,
        "tabs": [
            {"code": code, "title": _(title), "selected": code == tab,
             "url": _url(tab=code, unit=chosen)}
            for code, title in TABS
        ],
        "units": [
            {"code": ALL_UNITS, "title": _("Все точки"),
             "selected": chosen == ALL_UNITS, "url": _url(tab=tab, unit=ALL_UNITS)},
        ] + [
            {"code": row.code, "title": _unit_title(row.code, row.title),
             "selected": chosen == row.code, "url": _url(tab=tab, unit=row.code)}
            for row in sorted(whole.by_unit, key=lambda row: row.title)
        ],
        "range_title": _range_title(report),
        "months_count": len(report.months),
        "empty": not report.months,
        **_cost(report),
        **_churn(report),
        **_hours(report),
    })


def _url(*, tab: str, unit: str) -> str:
    """Адрес экрана с выбранными вкладкой и точкой — состояние живёт здесь."""
    address = reverse("people-analytics")
    query = [f"tab={tab}"] + ([f"unit={unit}"] if unit else [])
    return f"{address}?{'&'.join(query)}"


def _range_title(report) -> str:
    if not report.months:
        return ""
    if len(report.months) == 1:
        return month_title(report.months[0].period)
    return _("%(since)s — %(until)s") % {
        "since": month_title(report.months[0].period),
        "until": month_title(report.months[-1].period),
    }


def _cost(report) -> dict:
    """Вкладка «Стоимость труда»: динамика, показатели и два разреза."""
    top = max((month.payroll for month in report.months), default=Decimal("0"))
    last = report.months[-1] if report.months else None
    before = report.months[-2] if len(report.months) > 1 else None

    return {
        "bars": [
            {
                "month": month_title(month.period),
                "code": month.period.isoformat(),
                "value": money(month.payroll),
                "sub": _people(month.heads),
                "share": _share(month.share),
                "width": int(month.payroll / top * TOP_BAR) if top else 0,
            }
            for month in report.months
        ],
        "kpis": [
            {
                "label": _("ФОТ к выручке"),
                "value": _share(last.share) if last else EMPTY,
                "base": (_("за %(month)s") % {"month": month_title(last.period)}
                         if last and last.share is not None
                         else _("выручка приедет с коннектором Dodo IS")),
            },
            {
                "label": _("Стоимость часа"),
                "value": money(last.hour_cost) if last else EMPTY,
                "base": _delta_note(last, before, "hour_cost"),
            },
            {
                "label": _("ФОТ на человека"),
                "value": money(last.per_head) if last else EMPTY,
                "base": _delta_note(last, before, "per_head"),
            },
            {
                "label": _("ФОТ за период"),
                "value": money(report.payroll),
                "base": _("часов отработано: %(hours)s") % {"hours": hours(report.hours)},
            },
        ],
        "revenue_known": report.revenue_known,
        "cost_rows": [_cost_row(row, _unit_title) for row in report.by_unit],
        "group_rows": [_cost_row(row, _group_title) for row in report.by_group],
        "cost_total": {
            "payroll": money(report.payroll),
            "hours": hours(report.hours),
            "hour_cost": money(report.payroll / report.hours if report.hours else None),
            "heads": report.heads,
        },
    }


def _delta_note(last, before, field: str) -> str:
    """«против 412,00 в мае» — одно число ни о чём не говорит без прошлого месяца."""
    if last is None or before is None:
        return ""
    was = getattr(before, field)
    if was is None:
        return ""
    return _("против %(value)s в %(month)s") % {
        "value": money(was), "month": month_title(before.period),
    }


def _cost_row(row, title) -> dict:
    return {
        "code": row.code,
        "title": title(row.code, row.title),
        "payroll": money(row.payroll),
        "hours": hours(row.hours),
        "hour_cost": money(row.hour_cost),
        "heads": row.heads,
    }


def _churn(report) -> dict:
    """Вкладка «Кто уходит»: движение, текучесть и ушедшие поимённо."""
    return {
        "churn_meta": _("ушло %(left)s из %(base)s в среднем") % {
            "left": report.left, "base": _people(int(report.base)),
        },
        "min_base": people.MIN_BASE,
        "churn_total": {
            "hired": report.hired,
            "left": report.left,
            "turnover": _share(report.turnover),
            "base": _people(int(report.base)),
            "enough": report.enough_base,
        },
        "churn_rows": [_churn_row(row, _unit_title) for row in report.by_unit],
        "churn_groups": [_churn_row(row, _group_title) for row in report.by_group],
        "leavers": [
            {
                "name": row.name,
                "group": _group_title(row.group_code, row.group_title),
                "unit": _unit_title(row.unit_code, row.unit_title),
                "tenure": (_("%(count)s мес.") % {"count": row.tenure_months}
                           if row.tenure_months is not None else EMPTY),
                "left": month_title(row.dismissed_at),
            }
            for row in report.leavers
        ],
        "avg_tenure": (_("%(count)s мес.") % {"count": _one(report.avg_tenure)}
                       if report.avg_tenure is not None else EMPTY),
    }


def _one(value) -> str:
    """Число с одним знаком после запятой, разделителем языка страницы."""
    _thousands, decimal = separators()
    return f"{Decimal(value).quantize(Decimal('0.1'))}".replace(".", decimal)


def _churn_row(row, title) -> dict:
    return {
        "code": row.code,
        "title": title(row.code, row.title),
        "heads": row.heads,
        "hired": row.hired,
        "left": row.left,
        # Процент от малой базы не показывается вовсе: он выглядит как знание.
        "turnover": _share(row.turnover),
        "enough": row.enough_base,
        "base": _people(int(row.base)),
    }


def _hours(report) -> dict:
    """Вкладка «Часы и переработки» — за последний показанный месяц.

    Денег по коэффициенту переработки здесь нет: эталон считает их по 1,5, а в
    правилах Сербии коэффициент 1,26 и помечен как непроверенный. Поэтому рядом
    с часами стоит начисление человека за месяц — то самое, что в ведомости.
    """
    last = report.months[-1] if report.months else None
    return {
        "hours_month": month_title(last.period) if last else "",
        "over_hours": hours(report.over_hours),
        "over_people": _("у %(who)s") % {"who": _people(len(report.over_norm))},
        "over_share": _share(
            report.over_hours / last.hours * 100
            if last and last.hours else None
        ),
        "over_rows": [
            {
                "name": row.name,
                "unit": row.unit_title or _("Вся сеть"),
                "hours": hours(row.hours),
                "norm": hours(row.norm),
                "over": hours(row.over),
                "payroll": money(row.payroll),
            }
            for row in report.over_norm
        ],
    }
