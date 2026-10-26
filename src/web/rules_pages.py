"""Две страницы справочника правил рядом со списком: «Задано партнёром» и «Как устроено».

Эталон модуля 17 держит на одном экране четыре вкладки: профиль страны, юрлицо,
страны, как устроено. Здесь это отдельные страницы со ссылками (D081/D087: одна
страница — одна функция, переключателя без адреса нет). Профиль страны — сам
список `/rules/`. Сравнения стран нет: в базе одна страна, а второй колонке
нечем быть (см. журнал блока `rules`).
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from core.models import RuleFrameAttempt, RuleOverride

from . import rule_names, rule_targets, rules
from .format import EMPTY
from .rules_views import _assembled_at, _guard, _on_date, _tabs

# Сколько попыток выйти за рамку показывать. Журнал — след для аудита, а не
# лента: свежих двадцати хватает, чтобы увидеть, что происходит, а полный
# журнал по правилу открывается со страницы самого правила.
ATTEMPTS_SHOWN = 20


@login_required
@require_http_methods(["GET", "HEAD"])
def partner(request):
    """Что партнёр изменил поверх правил страны и что у него не вышло."""
    who, denied = _guard(request)
    if denied is not None:
        return denied

    on_date = _on_date(request)
    assembled, hidden = _assembled_at(who, on_date)
    preset = assembled.base if assembled is not None else {}
    names = rule_targets.titles_of_targets(who, on_date)
    active = (
        RuleOverride.objects.filter(tenant_id=who.tenant_id, valid_from__lte=on_date)
        .exclude(valid_to__lte=on_date)
        .order_by("path", "scope_type", "valid_from")
    )
    changed = [
        row for row in rule_targets.visible_only(active, names)
        if rules.is_visible(row.path, hidden)
    ]
    on = on_date.isoformat()
    attempts = [
        row for row in RuleFrameAttempt.objects.filter(tenant_id=who.tenant_id)
        .order_by("-created_at")[: ATTEMPTS_SHOWN * 3]
        if rules.is_visible(row.path, hidden)
    ][:ATTEMPTS_SHOWN]
    return render(request, "web/rules/partner.html", {
        "heading": _("Правила расчёта"),
        "tabs": _tabs("partner", on_date),
        "on_date": on,
        "changed": [
            {
                "path": row.path,
                "title": rule_names.name_of(preset, row.path),
                "url": f"{reverse('rule', args=[row.path])}?on={on}",
                "level": rules.level_title(row.scope_type),
                "who": names.get((row.scope_type, row.scope_id), ""),
                "value": rules.show(row.value),
                "since": row.valid_from.isoformat(),
            }
            for row in changed
        ],
        "attempts": [
            {
                "at": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
                "who": row.created_by_name or EMPTY,
                "path": row.path,
                "title": rule_names.name_of(preset, row.path),
                "url": f"{reverse('rule-attempts', args=[row.path])}?on={on}",
                "wanted": rules.show(row.wanted),
            }
            for row in attempts
        ],
    })


@login_required
@require_http_methods(["GET", "HEAD"])
def about(request):
    """Как устроено правило и как оно попадает в продукт — слова, без данных."""
    who, denied = _guard(request)
    if denied is not None:
        return denied

    on_date = _on_date(request)
    return render(request, "web/rules/about.html", {
        "heading": _("Правила расчёта"),
        "tabs": _tabs("about", on_date),
        "anatomy": [
            (_("Код"), _("Не меняется никогда. По нему правило находит расчёт.")),
            (_("Название"), _(
                "Подпись для человека на трёх языках. Выводится из словаря продукта, а не "
                "хранится в базе."
            )),
            (_("Тип"), _(
                "Число, переключатель, список, выбор из списка или текст. Тип берётся у "
                "действующего значения и задаёт поле ввода."
            )),
            (_("Значение"), _(
                "Своё у каждой страны. Партнёр, группа и человек могут задать своё поверх — с "
                "датой."
            )),
            (_("Кто меняет"), _(
                "Закон, рамка страны или партнёр. Закон не меняется вовсе, рамка допускает "
                "только более строгое значение."
            )),
            (_("Что читает"), _(
                "Модули продукта, которые берут это значение. Список привязан к исходникам и "
                "проверяется тестом."
            )),
            (_("История"), _(
                "Значения по датам. Закрытый месяц считается по версии, действовавшей в нём."
            )),
        ],
        "laws": [
            (_("Правило — строка, а не поле"), _(
                "Все правила лежат в одной таблице с одинаковым устройством. Новое правило не "
                "требует переписывать модули: они спрашивают значение по коду."
            )),
            (_("Рамку двигает закон, не администратор"), _(
                "Партнёр становится строже рамки, но никогда мягче. Попытка выйти за рамку "
                "записывается в журнал с автором и временем — это след для аудита, а не защита "
                "от ошибки."
            )),
            (_("Значение всегда с датой"), _(
                "У правила нет «текущего значения» — есть значение на дату. Поэтому закрытый "
                "месяц считается по правилам своего времени, даже если страна уже сменила "
                "ставку."
            )),
        ],
        "steps": [
            ("01", _("Правило заводит разработка"), _(
                "Код, тип и список читателей — часть кода, потому что от них зависит расчёт. "
                "Это единственный шаг, где нужен релиз."
            )),
            ("02", _("Значение страны задаёт администратор платформы"), _(
                "Первичная загрузка из файла страны или новая версия правил страны с экрана "
                "правила — только у того, кто ведёт правила страны."
            )),
            ("03", _("Партнёр уточняет внутри рамки"), _(
                "Партнёр, группа или человек задают своё значение с даты. Закрытые месяцы от "
                "этого не двигаются."
            )),
        ],
    })
