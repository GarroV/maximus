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

**Три режима — три страницы** (T226, D081): «Стоимость труда», «Кто уходит»,
«Часы и переработки». Эталон рисует их вкладками одного экрана; владелец решил
иначе — одна страница, одна функция, и каждая стоит своим пунктом левой панели.
У каждой свой корень адреса: пункт панели выделяется по корню (`in_section`), и
общий корень выделил бы все три разом.

**Выбор точки — обычные ссылки.** Состояние страницы целиком лежит в адресе,
поэтому его можно прислать другому человеку, и оно переживает перезагрузку.
Прежний адрес с вкладкой в параметре (`/analytics/people/?tab=…`) не умирает:
присланная ссылка ведёт на ту страницу, которую имел в виду отправитель.
"""
from __future__ import annotations

from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from reports import people

from .format import EMPTY, hours, money, separators
from .i18n import month_title
from .principal import get_current_principal

__all__ = ["analytics", "churn_page", "cost_page", "hours_page"]

# Страницы в порядке вкладок эталона: сначала деньги, потом люди, потом часы.
# Код — прежнее значение `?tab=`, по нему старый адрес находит свою страницу.
PAGES = {
    "cost": ("people-cost", gettext_noop("Стоимость труда")),
    "churn": ("people-churn", gettext_noop("Кто уходит")),
    "hours": ("people-hours", gettext_noop("Часы и переработки")),
}
FIRST_PAGE = "cost"

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
    """Прежний адрес экрана: ведёт на страницу, которую называла его вкладка.

    Ссылки вида `/analytics/people/?tab=churn&unit=NS1` уже разосланы и лежат в
    закладках; ответить на них «нет такой страницы» значило бы сломать то, ради
    чего состояние и держалось в адресе. Неизвестная вкладка — первая страница,
    как и было на прежнем экране.
    """
    code = request.GET.get("tab", FIRST_PAGE)
    route, _title = PAGES.get(code, PAGES[FIRST_PAGE])
    return redirect(_url(route, unit=request.GET.get("unit", ALL_UNITS)))


@login_required
def cost_page(request):
    """Стоимость труда: динамика ФОТ, плитки, разрезы по точке и группе."""
    report, context = _frame(request, "cost")
    return render(request, "web/reports/people_cost.html", {
        **context, "subtitle": _range_title(report), **_cost(report),
    })


@login_required
def churn_page(request):
    """Кто уходит: приняли и ушли, текучесть с базой рядом, ушедшие поимённо."""
    report, context = _frame(request, "churn")
    return render(request, "web/reports/people_churn.html", {
        **context, "subtitle": _range_title(report), **_churn(report),
    })


@login_required
def hours_page(request):
    """Часы и переработки за последний показанный месяц."""
    report, context = _frame(request, "hours")
    data = _hours(report)
    return render(request, "web/reports/people_hours.html", {
        **context, "subtitle": data["hours_month"], **data,
    })


def _frame(request, code: str):
    """Общее у трёх страниц: отчёт на выбранную точку и ссылки выбора точки.

    Права у страниц своего нет и не заводится: они ничего не пишут, а видят
    ровно то, что база отдаёт роли открывшего (D014). Управляющий точки увидит
    здесь свою точку — и не потому, что мы отфильтровали, а потому что политики
    базы больше ему не дали.

    Полный отчёт собирается один раз без фильтра: из него берётся и список точек
    (только тех, где кто-то показан), и проверка, что спрошенная точка вообще
    существует для этой роли.
    """
    who = get_current_principal(request)
    tenant_id = who.tenant_id if who else None
    route, title = PAGES[code]

    asked = request.GET.get("unit", ALL_UNITS)
    whole = people.build(tenant_id)
    known = {row.code for row in whole.by_unit}
    chosen = asked if asked in known else ALL_UNITS
    report = whole if chosen == ALL_UNITS else people.build(tenant_id, unit_filter=chosen)

    return report, {
        "page_title": _(title),
        "units": [
            {"code": ALL_UNITS, "title": _("Все точки"),
             "selected": chosen == ALL_UNITS, "url": _url(route, unit=ALL_UNITS)},
        ] + [
            {"code": row.code, "title": _unit_title(row.code, row.title),
             "selected": chosen == row.code, "url": _url(route, unit=row.code)}
            for row in sorted(whole.by_unit, key=lambda row: row.title)
        ],
        "months_count": len(report.months),
        "empty": not report.months,
    }


def _url(route: str, *, unit: str) -> str:
    """Адрес страницы с выбранной точкой — состояние живёт здесь."""
    address = reverse(route)
    return f"{address}?{urlencode({'unit': unit})}" if unit else address


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
                **_change(last, before, "share", points=True),
                "base": (_("за %(month)s") % {"month": month_title(last.period)}
                         if last and last.share is not None
                         else _("выручка приедет с коннектором Dodo IS")),
            },
            {
                "label": _("Стоимость часа"),
                "value": money(last.hour_cost) if last else EMPTY,
                **_change(last, before, "hour_cost"),
            },
            {
                "label": _("ФОТ на человека"),
                "value": money(last.per_head) if last else EMPTY,
                **_change(last, before, "per_head"),
            },
            {
                "label": _("ФОТ за период"),
                "value": money(report.payroll),
                "delta": "",
                "base": _period_share(report),
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


def _change(last, before, field: str, *, points: bool = False) -> dict:
    """Изменение к прошлому месяцу: знак, величина и подпись «против чего».

    Одно число ни о чём не говорит без прошлого месяца (эталон модуля 12). Нет
    прошлого месяца или значения в нём — изменения нет, и это сказано словами:
    пустое место под числом читалось бы как «не изменилось».

    Цвет: рост стоимости — красный, падение — зелёный. Это не вывод отчёта, а
    направление денег: плитки здесь все про затраты.
    """
    now = getattr(last, field) if last else None
    if now is None:
        return {"delta": "", "base": ""}
    was = getattr(before, field) if before else None
    if was is None:
        return {"delta": "", "base": _("прошлого месяца в отчёте нет — сравнить не с чем")}
    diff = Decimal(now) - Decimal(was)
    size = (_one(abs(diff)) + " " + _("п.п.")) if points else money(abs(diff))
    return {
        "delta": ("+" if diff > 0 else "\u2212" if diff < 0 else "±") + size,
        "delta_class": "num--down" if diff > 0 else "num--up" if diff < 0 else "num--muted",
        # Месяц в скобках, а не «в мае»: название месяца приходит в именительном
        # падеже, и «в Май 2026» читалось бы как ошибка.
        "base": _("против %(value)s (%(month)s)") % {
            "value": _share(was) if points else money(was),
            "month": month_title(before.period),
        },
    }


def _period_share(report) -> str:
    """Подпись под ФОТ за период — доля от выручки тех же месяцев, как в эталоне.

    Считается только если выручка есть в КАЖДОМ показанном месяце: доля от
    выручки половины месяцев завышала бы ответ вдвое и выглядела бы как знание.
    """
    revenue = [month.revenue for month in report.months]
    if not revenue or not all(revenue):
        return _("доля от выручки — выручки в продукте пока нет")
    total = sum(revenue, Decimal("0"))
    return _("%(share)s от выручки %(revenue)s") % {
        "share": _share(report.payroll / total * 100), "revenue": money(total),
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
        "over_people": _("%(over)s из %(heads)s") % {
            "over": len(report.over_norm), "heads": _people(last.heads if last else 0),
        },
        "over_warn": bool(report.over_norm),
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
