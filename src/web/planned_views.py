"""Экраны, которых ещё нет, и входы в те, что живут внутри периода (D066).

Здесь два разных ответа на одну жалобу владельца — «в демо явно не весь
функционал», сказанную 31.08.2026 второй раз за неделю (первый разбор —
`docs/design-gap.md`, 26.08). Оба раза причина была не в том, что продукт беден,
а в том, что по нему нельзя пройти.

**Заглушка ненаписанного экрана.** Пункт меню есть у каждого раздела эталона, и
у ненаписанного он ведёт на страницу, которая называет своё состояние словами:
что здесь будет и на каком месте очереди стоит. Прежнее правило прятало такие
пункты совсем, и смотрящий читал отсутствие пункта как отсутствие замысла.

**Вход в экран, который живёт внутри месяца.** Ведомость, закрытие, P&L и
сверка у нас открываются из конкретного периода, а в шапке пункт обязан вести
куда-то без выбора месяца. Поэтому пункт ведёт в **последний заведённый месяц** —
тот, с которым человек и работает. Месяцев нет вовсе — ведёт в их список, где
стоит кнопка «Завести месяц»: тупика не остаётся ни в одном случае.

Почему не «выбери период, потом отчёт»: лишний экран между пунктом меню и
работой — то самое место, где посетитель демо разворачивается.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from . import stages

__all__ = ["closing", "payroll_sheet", "planned", "pnl", "reconcile", "timesheet_current"]


@login_required
def planned(request, code: str):
    """Страница ненаписанного экрана: что здесь будет и когда."""
    screen = next(
        (item for item in stages.planned_screens() if item.code == code), None,
    )
    if screen is None:
        raise Http404("no such planned screen")
    return render(request, "web/planned.html", {"title": screen.title, "screen": screen})


def _latest_period():
    """Последний заведённый месяц или `None`.

    Сортировка по учётному месяцу, а не по дате создания: месяц могли завести
    задним числом, и человек всё равно ждёт увидеть самый поздний.
    """
    from core.models import Period

    return Period.objects.order_by("-period").first()


def _inside_latest(route: str, anchor: str = ""):
    """Адрес экрана внутри последнего месяца — или список месяцев, если их нет."""
    period = _latest_period()
    if period is None:
        return reverse("periods")
    return reverse(route, args=[period.id]) + anchor


@login_required
def payroll_sheet(request):
    """«Ведомость» из шапки — ведомость по сотрудникам последнего месяца.

    До T241 вела на страницу месяца, туда же, куда «Закрытие месяца», и два
    пункта меню открывали одно и то же (#306): человек нажимал «Ведомость» и
    видел степпер месяца. Ведомость — свой экран (модуль 9 эталона).
    """
    return redirect(_inside_latest("period-sheet"))


@login_required
def closing(request):
    """«Закрытие месяца» из шапки.

    Ведёт на страницу месяца с якорем на блок готовности: закрытие (модуль 4
    эталона) живёт на ней. Ведомость — отдельный экран (`payroll_sheet`).
    """
    return redirect(_inside_latest("period", "#closing"))


@login_required
def pnl(request):
    """«P&L» из шапки — отчёт последнего месяца."""
    return redirect(_inside_latest("period-pnl"))


@login_required
def reconcile(request):
    """«Сверка с таблицей» из шапки — сверка последнего месяца."""
    return redirect(_inside_latest("period-reconcile"))


def _working_period():
    """Рабочий месяц: последний открытый, а если открытых нет — последний."""
    from core.models import Period

    return (
        Period.objects.filter(status="open").order_by("-period").first()
        or _latest_period()
    )


@login_required
def timesheet_current(request):
    """«Табель» управляющего точки — сразу сетка часов рабочего месяца (#315).

    Дело управляющего — часы своей точки, а не выбор месяца из списка: список
    месяцев ведёт на страницу расчёта, где ему нечего делать, кроме как читать
    отказы по правам. Точку срезает база (D014), поэтому адрес у всех один.
    Месяцев нет — список месяцев, где сказано, кто их заводит.
    """
    period = _working_period()
    if period is None:
        return redirect("periods")
    return redirect(reverse("timesheets", args=[period.id]))
