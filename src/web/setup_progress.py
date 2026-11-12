"""Настройка нового партнёра: шесть шагов до первого месяца (D100).

Владелец 12.11.2026: «проработай онбординг. чтобы нового партнера прям за ручку
провести». До этого человек после первого входа попадал на пустой список
месяцев, а о справочниках ему не говорил никто — хотя считать месяцу нечего,
пока их нет.

Состояние шага — **факт о данных**, а не отметка «я это сделал»: отметка
разъехалась бы с базой в тот день, когда кто-то удалит точку или завёл людей
импортом. Поэтому каждый шаг — один запрос «есть ли такое», под политиками
самой базы (RLS), как и остальные экраны.

Календарь общий для страны (#100): если его завёл другой партнёр, шаг зелёный
сам — нормы часов хватает, и просить завести второй раз было бы неправдой.
"""
from __future__ import annotations

from datetime import date

from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from . import directory, permissions


def _has_legal_entity(who) -> bool:
    from core.models import LegalEntity

    return LegalEntity.objects.exists()


def _has_unit(who) -> bool:
    from core.models import Unit

    return Unit.objects.exists()


def _has_group(who) -> bool:
    from core.models import EmployeeGroup

    return EmployeeGroup.objects.exists()


def _has_employee_with_terms(who) -> bool:
    """Есть человек с версией условий, которая действует сейчас или начнётся.

    Версия, закончившаяся в прошлом, не считается: считать по ней новый месяц
    нечем, и зелёная отметка обманула бы.
    """
    from core.models import EmploymentTerm

    today = date.today()
    return EmploymentTerm.objects.filter(
        Q(valid_to__isnull=True) | Q(valid_to__gt=today),
    ).exists()


def _has_calendar(who) -> bool:
    """Календарь страны партнёра хотя бы на один месяц, и с нормой часов."""
    from core.models import Calendar

    country = directory.country_of(who.tenant_id)
    return Calendar.objects.filter(country_code=country, norm_hours__gt=0).exists()


def _has_period(who) -> bool:
    from core.models import Period

    return Period.objects.exists()


def _has_closed_period(who) -> bool:
    from core.models import Period

    return Period.objects.filter(status="closed").exists()


def _steps_spec():
    # Ссылка последнего шага ведёт на форму «Завести месяц» на списке месяцев:
    # отдельной страницы у неё нет.
    return (
        ("legal_entity", _("Юрлицо"),
         _("Бухгалтерия работает с юрлицом: к нему привязываются точки."),
         reverse("directory-legal-entity-new"), _has_legal_entity),
        ("unit", _("Точка"),
         _("Часы и расходы вносятся на точку — пиццерию."),
         reverse("directory-unit-new"), _has_unit),
        ("group", _("Группа сотрудников"),
         _("Группа задаёт, как считается оплата и в каком регистре учёта она идёт."),
         reverse("directory-group-new"), _has_group),
        ("employee", _("Сотрудник с условиями найма"),
         _("Без условий найма человек не попадает в табель: считать его нечем."),
         reverse("directory-employee-new"), _has_employee_with_terms),
        ("calendar", _("Производственный календарь"),
         _("Из него берётся норма часов месяца. Он общий для страны: если его уже завели, шаг сделан."),
         reverse("directory-calendar-new"), _has_calendar),
        ("period", _("Первый месяц"),
         _("Месяц, в котором вы вносите часы и считаете ведомость."),
         reverse("periods") + "#open-month", _has_period),
    )


def setup_steps(who) -> list[dict]:
    """Шаги настройки партнёра: `{key, title, done, url, hint}`, в порядке работы."""
    return [
        {"key": key, "title": title, "done": check(who), "url": url, "hint": hint}
        for key, title, hint, url, check in _steps_spec()
    ]


def setup_card(who) -> dict | None:
    """Что показать сверху списка месяцев — или `None`, если ничего.

    Карточку видит тот, кто ведёт справочники: остальным она предлагала бы
    действия, которых им не разрешено делать. После первого утверждённого
    месяца карточки нет вовсе — партнёр настроен, он работает.
    """
    if not permissions.has(who, permissions.DIRECTORY_MANAGE):
        return None
    if _has_closed_period(who):
        return None
    steps = setup_steps(who)
    pending = [step for step in steps if not step["done"]]
    return {
        "steps": steps,
        "total": len(steps),
        "done_count": len(steps) - len(pending),
        "next_key": pending[0]["key"] if pending else None,
        "all_done": not pending,
    }
