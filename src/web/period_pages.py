"""
Страницы месяца, разобранные по функциям (D081, D087, T230).

Раньше `/periods/<id>/` держала всё сразу: ход расчёта, готовность к закрытию,
утверждение, ведомость с разрезами и выгрузками, итоги по регистрам, историю
переходов и правку задним числом. Теперь это сводка (`views.period_page`) и
четыре страницы, на каждой — одна функция:

    .../sheet/      ведомость: разрезы, выгрузки, заморозка строк, след суммы
    .../registers/  итоги по регистрам учёта
    .../history/    кто и когда переводил период из состояния в состояние
    .../retro/      правка задним числом: расхождение и перенос разницы

Права у каждой страницы те же, что были у этой части на старой: видимость
регистров отдают политики базы и срез `build_slice`, а не шаблон; действия
проверяют те же `permissions`. Новых прав здесь нет — это разбор экрана, а не
изменение расчёта.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from core.models import Payrun, Timesheet
from payrun import freezing, lifecycle, retro
from payrun.errors import PayrunRefused
from reports import export as exports
from reports.sheet import build_slice

from . import permissions, runslice
from .format import CodedTitle, cut_title, ledger_title, money
from .i18n import month_title
from .labels import labeller
from .principal import get_current_principal
from .views import (
    FREEZE_NEEDS_WHOLE_RUN,
    GROUP_TITLES,
    cut_url,
    find_period,
    first_of_payslip,
    trace_url,
)


def sees_payroll(who) -> bool:
    """Единственный вход к суммам расчёта: ведомость, итоги, правка задним числом.

    Суммы видит тот, у кого есть `payroll.view` или действие, которое без них
    не выполнить (`permissions.SEES_SUMS`, T236; тот же список у функции базы
    `app_sees_payroll`). По этой функции закрываются все страницы сумм и
    решает сводка месяца, показывать ли итог, число людей и расхождение.
    Второго места, где решалось бы то же самое, быть не должно.
    """
    return (
        who is not None and who.tenant_id is not None and permissions.sums_visible(who)
    )


def payroll_page(view):
    """Страница с суммами расчёта: тот, кому они не положены, получает 404.

    404, а не объяснение: ответ не должен отличаться от ответа на несуществующий
    месяц и рассказывать, что в нём есть что показать.
    """
    from functools import wraps

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not sees_payroll(get_current_principal(request)):
            raise Http404("период не найден")
        return view(request, *args, **kwargs)

    return wrapped


def _head(period) -> dict:
    """Общая шапка подстраницы: название месяца, состояние, возврат к сводке.

    Состояние расчёта несёт свой код: плашка красится по нему, а не по тексту,
    который зависит от языка.
    """
    payrun = Payrun.objects.filter(tenant=period.tenant, period=period.period).first()
    return {
        "period": period,
        "month": month_title(period.period),
        "payrun_status": (
            CodedTitle(lifecycle.status_title(payrun.status), payrun.status)
            if payrun else ""
        ),
        "payrun": payrun,
    }


# ---------------------------------------------------------------- ведомость


def sheet_page(request, period, *, error=None, error_title=None, status=200):
    """Ведомость периода с разрезом, выгрузками и заморозкой строк.

    Ведомость собирается **только из компонентов выплаты** — суммарные поля
    ведомости не имеют политики видимости регистров и выдали бы скрытое
    вычитанием. Поэтому и итог здесь равен сумме показанных строк (D023).

    Разрез по регистру приезжает из адреса и разбирается блоком `reports`
    (T028): страница только показывает то, что он отдал. Собирать срез здесь
    значило бы собирать его второй раз в выгрузке — и разъехаться с экраном.
    Разрез живёт в адресе, а не в сессии: ссылку на «официальный срез июня»
    можно отправить коллеге, и он увидит то же самое — в пределах того, что
    видно ему.
    """
    head = _head(period)
    view = build_slice(period.tenant_id, period.period, request.GET.get("ledger", ""))
    sheet = view.sheet
    # Подпись компонента заморожена в момент расчёта и говорит на языке правил.
    # Чтобы колонка говорила на языке читателя, она берётся заново по коду — из
    # правил ТОГО ЖЕ периода (T092, разбор в `web/labels.py`).
    label = labeller(period.tenant_id, period.tenant.country_code, period.period)
    seen_payslips: set = set()
    who = get_current_principal(request)
    payrun_status = head["payrun"].status if head["payrun"] else None
    frozen = payrun_status == lifecycle.APPROVED
    whole_run = runslice.sees_whole_run(period.tenant_id)

    # Заморозка спорной строки (T027). В утверждённом периоде её не предлагают:
    # там заморожен весь расчёт, и база новую заморозку отвергнет — предлагать
    # кнопку значило бы обещать невозможное. Право проверяется отдельно от
    # состояния, как и у остальных действий: две разные причины «кнопки нет» не
    # смешиваются.
    freeze_denied = permissions.explain(who, permissions.PAYSLIP_FREEZE) if not frozen else ""
    # Третье условие, независимое от права и от состояния периода (T101):
    # заморозка относится к строке **целиком**, поэтому ставит её тот, кому база
    # отдаёт весь её расчёт — все регистры учёта. Роли с одним официальным
    # регистром кнопку не предлагают: она распоряжалась бы числами, которых ей
    # не показывают. Причина называется словами, а не оставляется молчанием —
    # исчезнувшая без объяснения кнопка читается как поломка.
    if not freeze_denied and not frozen and not freezing.rows_are_freezable(period.tenant_id):
        freeze_denied = _(FREEZE_NEEDS_WHOLE_RUN)

    employees = Timesheet.objects.filter(tenant=period.tenant, period=period.period).count()

    return render(
        request,
        "web/period_sheet.html",
        {
            **head,
            "title": _("Ведомость") + " · " + head["month"],
            "error": error,
            "error_title": error_title or _("Действие не выполнено."),
            "sheet": {
                "columns": [label(column.code, column.title) for column in sheet.columns],
                # Ярус групп над колонками (issue #163). Названия переводятся
                # здесь, а не в сборке ведомости: `payrun/sheet.py` — чистый
                # Python без Django, и обращаться из него к каталогу переводов
                # значило бы втащить туда веб.
                "groups": [
                    {"title": GROUP_TITLES.get(group.code, group.code), "span": group.span}
                    for group in sheet.groups
                ],
                "rows": [
                    {
                        "employee": row.employee,
                        "unit": row.unit,
                        # Куда идут деньги этого человека (D055, T221): колонка
                        # называет точку строки, подпись — что сумма делится
                        # между несколькими точками или уходит на всю сеть.
                        "units_note": _units_note(row),
                        "ledger": ledger_title(row.ledger),
                        "cells": [
                            money(row.amounts.get(column.code)) for column in sheet.columns
                        ],
                        "total": money(row.total),
                        # Заморозка у человека одна на все его регистры, а строк
                        # у него может быть две. Метка стоит у каждой (иначе
                        # половина ведомости выглядела бы живой), а действие —
                        # один раз: две одинаковые кнопки об одном и том же
                        # читаются как разные.
                        "payslip_id": row.payslip_id,
                        "frozen": row.frozen,
                        "freeze_reason": row.freeze_reason,
                        "show_action": first_of_payslip(row.payslip_id, seen_payslips),
                        # След расчёта строки (T029). Разрез в ссылке — регистр
                        # самой строки: объяснять её итог надо тем же срезом,
                        # иначе след не сойдётся с числом, по которому кликнули.
                        "trace_url": trace_url(row),
                        # Строка разницы обязана объяснить себя словами: без
                        # месяца-источника это непонятная сумма в чужом месяце.
                        "is_retro": row.is_retro,
                        "retro_title": (
                            retro.month_title(row.retro_source) if row.is_retro else ""
                        ),
                    }
                    for row in sheet.rows
                ],
                "column_totals": [
                    money(sheet.column_totals.get(column.code)) for column in sheet.columns
                ],
                "employees": sheet.employees,
            },
            # Переключатель разреза (T028). Кнопок либо нет вовсе, либо их
            # больше одной: ряд из единственной кнопки намекал бы роли с одним
            # регистром, что где-то есть и другие.
            "cuts": [
                {
                    "code": cut.code,
                    "title": cut_title(cut.code),
                    "selected": cut.selected,
                    "url": cut_url(period, cut.code),
                }
                for cut in view.cuts
            ],
            "cut_title": cut_title(view.cut) if view.cut else "",
            # Код выбранного разреза отдельно от списка кнопок: его берут
            # адреса выгрузок. Второго места, где хранился бы выбранный разрез,
            # быть не должно — иначе экран и файл разъедутся молча.
            "cut_code": view.cut or "",
            "show_registers_link": len(sheet.ledger_totals) > 1,
            # Есть ли в периоде НДС (T146): пока налога нет ни у одной строки,
            # вторая кнопка «Строки для P&L с НДС» отдавала бы тот же файл.
            "has_vat": exports.vat_exists(period.tenant_id, period.period),
            # Чего не будет в файле «Строки для P&L» и почему (T141). Сказано
            # рядом с кнопкой, а не только внутри файла.
            "exports_note": exports.taxes_note(exports.taxes_missing(
                has_taxes=exports.taxes_exist(period.tenant_id, period.period),
                has_rows=bool(sheet.rows),
                cut=view.cut,
                whole_run=whole_run,
            )),
            "total": money(sheet.total) if sheet else money(None),
            "employees_in_timesheet": employees,
            "frozen": frozen,
            "can_freeze": not freeze_denied and not frozen,
            "freeze_denied": freeze_denied,
            "froze": request.GET.get("froze") == "1",
            "released": request.GET.get("released") == "1",
            "calculate_denied": permissions.explain(who, permissions.PAYRUN_CALCULATE),
        },
        status=status,
    )


def _units_note(row) -> str:
    from core.people_units import units_note

    return units_note(row.cost_units, row.unit)


@login_required
@payroll_page
def sheet(request, period_id):
    return sheet_page(request, find_period(period_id))


# ------------------------------------------------------------------ регистры


@login_required
@payroll_page
def registers(request, period_id):
    """Итоги по регистрам учёта: сколько в каждом, и что они дают в сумме.

    Берётся полный срез роли без разреза: страница отвечает на вопрос «сколько
    в каждом регистре», а выбор одного регистра сделал бы ответом один из рядов.
    Регистр, которого роль не видит, не попадает ни строкой, ни следом в сумме
    (D023) — это отдаёт тот же `build_slice`, что и ведомость.
    """
    period = find_period(period_id)
    head = _head(period)
    sheet_view = build_slice(period.tenant_id, period.period, "").sheet
    return render(
        request,
        "web/period_registers.html",
        {
            **head,
            "title": _("Итоги по регистрам") + " · " + head["month"],
            "ledgers": [
                {"title": ledger_title(name), "total": money(amount)}
                for name, amount in sheet_view.ledger_totals
            ],
            "total": money(sheet_view.total) if sheet_view else money(None),
            "has_rows": bool(sheet_view.rows),
            "sheet_url": reverse("period-sheet", args=[period.id]),
        },
    )


# ------------------------------------------------------------------- история


@login_required
def history(request, period_id):
    """История переходов периода: когда, что стало, кто, по какой причине.

    Журнал ведёт база триггером (T023) — здесь только показ. Второго журнала
    рядом не заводится: перехода без записи не бывает ни на одном пути записи.
    """
    period = find_period(period_id)
    head = _head(period)
    return render(
        request,
        "web/period_history.html",
        {
            **head,
            "title": _("История периода") + " · " + head["month"],
            "history": lifecycle.history(head["payrun"]),
        },
    )


# ------------------------------------------------------ правка задним числом


def retro_page(
    request, period, *, error=None, error_title=None, details=(),
    run_refusal=False, status=200,
):
    """Правка задним числом: расхождение закрытого месяца с сегодняшними данными.

    Расхождение показывается только у утверждённого месяца: у открытого правка
    входных данных — обычная работа, её пересчитывают. Спрашивается в срезе
    роли (T085): свежий расчёт движок считает целиком, и разница без среза
    равнялась бы тому, чего человеку не видно. Месяц-получатель назван словами:
    молча выбранный получатель — это сумма, появившаяся неизвестно где.
    """
    head = _head(period)
    who = get_current_principal(request)
    label = labeller(period.tenant_id, period.tenant.country_code, period.period)
    payrun_status = head["payrun"].status if head["payrun"] else None
    frozen = payrun_status == lifecycle.APPROVED
    mode = retro.mode(period.tenant_id)
    # Подробности отказа собраны по всему расчёту партнёра и показываются не
    # каждому роли (T114): вместо списка стоит объяснение, почему его нет.
    whole_run = runslice.sees_whole_run(period.tenant_id)
    if not whole_run:
        details = []
    found = (
        retro.drift(
            period.tenant_id, period.period,
            visible_ledgers=who.visible_ledgers if who else [],
        )
        if frozen else retro.Drift()
    )
    retro_denied = permissions.explain(who, permissions.RETRO_POST) if found else ""
    return render(
        request,
        "web/period_retro.html",
        {
            **head,
            "title": _("Правки задним числом") + " · " + head["month"],
            "frozen": frozen,
            "error": error,
            "error_title": error_title or _("Разница не перенесена."),
            "details": list(details),
            "details_denied": (
                runslice.details_note(whole_run=whole_run) if run_refusal else ""
            ),
            "retro_mode": mode,
            "retro_drift": found,
            "retro_error": found.error,
            "retro_total": money(found.total) if found else "",
            "retro_target": (
                retro.month_title(retro.next_open_period(period.tenant_id, period.period))
                if found and mode == retro.DELTA else ""
            ),
            "retro_lines": [
                {
                    "employee": line.employee,
                    "title": label(line.code, line.title),
                    "ledger": ledger_title(line.ledger),
                    "amount": money(line.amount),
                }
                for line in found.lines
            ],
            # Кнопки нет по двум разным причинам, и они не смешиваются: партнёр
            # ведёт учёт пересчётом (тогда о праве говорить нечего) или права
            # нет (тогда на месте кнопки тот же текст, которым ответит отказ).
            "can_post_retro": bool(found) and mode == retro.DELTA and not retro_denied,
            "retro_denied": retro_denied if mode == retro.DELTA else "",
            "retro_posted": request.GET.get("retro") == "1",
        },
        status=status,
    )


@login_required
@payroll_page
def period_retro(request, period_id):
    """GET — страница правок задним числом; POST — «перенести разницу»."""
    period = find_period(period_id)
    if request.method == "POST":
        return _retro_post(request, period)
    return retro_page(request, period)


def _retro_post(request, period):
    """«Перенести разницу»: закрытый месяц остаётся прежним, разница едет вперёд.

    Порядок проверок тот же, что у расчёта и у цикла (T064): право → всё
    остальное. Человек без права не должен сначала узнавать, есть ли вообще
    расхождение, — до него ему нет дела.

    В сам закрытый период не пишется ничего, и это видно по коду: перенос —
    вставка в свою таблицу. Гарантией это, однако, не является — её держит
    сторож `payrun_frozen_guard` (T023), отвергающий любую запись в утверждённый
    расчёт на любом пути.
    """
    who = get_current_principal(request)
    if who is None or who.tenant_id is None:
        # Вошёл, но ни к какому партнёру не приписан: периода для него нет.
        raise Http404("период не найден")

    try:
        permissions.check(who, permissions.RETRO_POST)
        retro.post(
            tenant_id=period.tenant_id,
            source=period.period,
            actor_id=who.user_id,
            visible_ledgers=who.visible_ledgers,
        )
    except permissions.PermissionRefused as refusal:
        return retro_page(
            request, period, error=refusal.message,
            error_title=_("Разница не перенесена."), status=refusal.http_status,
        )
    except PayrunRefused as refusal:
        # Регистры отказ называет кодами; человеку показываем их названия.
        details = refusal.details or [ledger_title(name) for name in refusal.ledgers]
        return retro_page(
            request, period, error=refusal.message, details=details,
            run_refusal=True,
            error_title=_("Разница не перенесена."), status=refusal.http_status,
        )

    # Перенаправление после записи: обновление страницы не переносит второй раз.
    # Второй перенос и так не нашёл бы расхождения (оно уже вычтено), но
    # надеяться на это вместо перенаправления значило бы обещать, что арифметика
    # заменяет защиту от повторной отправки формы.
    return redirect(reverse("period-retro", args=[period.id]) + "?retro=1")
