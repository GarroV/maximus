"""Сотрудник тремя страницами: карточка, условия найма, точки затрат (D081, T229).

Прежде всё это было одним экраном `/directory/employees/<id>/`: карточка, «что
попадёт в расчёт», история условий найма, надбавки, форма новой версии, набор
точек и форма набора с даты — шесть функций, четыре формы и переключатель
`what` в скрытом поле, по которому одна и та же отправка решала, что писать.
D081 требует «одна страница — одна функция», поэтому экран разобран:

- `/directory/employees/<id>/` — сам человек: карточка (имя, ключ, даты) и
  предпросчёт месяца по действующим условиям;
- `.../terms/` — условия найма: история версий, надбавки, новая версия с даты;
- `.../units/` — точки, между которыми делятся затраты, и набор с даты;
- `.../pay/` — выплаты по месяцам, как и прежде (`person_views.py`).

Между ними — ряд ссылок `cuts` под именем человека: страницы одного человека
видны все сразу, а не прячутся в раскрытии.

**Права на каждой странице те же, что были у карточки, и на `GET`, и на
`POST`.** Человек ищется до проверки права: скрытый (чужой, несуществующий,
чужого регистра) отвечает 404 всем одинаково (D023, T173). Читать может любой,
кого завели к партнёру, — строки режет база. Записать — только с
`directory.manage`; без него `POST` получает 403 словами, а на месте формы стоят
те же слова (`permissions.explain`), а не пустота (T072). Отказ управляющему по
скрытой строке «вся сеть» — нейтральный 409, как сделано в T221
(`directory.save_units`).
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from core.models import Employee, EmploymentTerm

from . import directory, permissions
from .dbrefusal import BadInput, ConstraintRefused, saving
from .directory_views import (
    _allowances_of,
    _effective_measure,
    _employee_or_404,
    _measure_title,
    _network_option,
    _person_fields,
    _preset_now,
    _preview_of,
    _reader,
    _refusal,
    _save_person,
    _save_terms,
    _save_units,
    _saved_notices,
    _scheme_title,
    _terms_fields,
    _unit_options,
    _unit_rows,
    _visible_terms,
)
from .format import EMPTY, day, exact, ledger_title

__all__ = ["employee", "employee_terms", "employee_units"]

# Страницы человека: имя адреса и подпись в ряду ссылок. Порядок — порядок ряда.
CARD, TERMS, UNITS = "directory-employee", "directory-employee-terms", "directory-employee-units"
PAY = "directory-employee-pay"


def _page_titles() -> dict:
    return {
        CARD: _("Карточка"),
        TERMS: _("Условия найма"),
        UNITS: _("Точки затрат"),
        PAY: _("Выплаты по месяцам"),
    }


@login_required
@require_http_methods(["GET", "POST"])
def employee(request, employee_id):
    """Карточка: имя, внешний ключ, даты приёма и увольнения; предпросчёт месяца."""
    return _page(
        request, employee_id, here=CARD, save=_save_card,
        template="web/directory/employee.html", context=_card_context,
    )


@login_required
@require_http_methods(["GET", "POST"])
def employee_terms(request, employee_id):
    """Условия найма: история версий, надбавки и новая версия с даты."""
    return _page(
        request, employee_id, here=TERMS, save=_save_terms,
        template="web/directory/employee_terms.html", context=_terms_context,
    )


@login_required
@require_http_methods(["GET", "POST"])
def employee_units(request, employee_id):
    """Точки, между которыми делятся затраты на человека, и набор с даты."""
    return _page(
        request, employee_id, here=UNITS, save=_save_units,
        template="web/directory/employee_units.html", context=_units_context,
    )


def _save_card(request, who, person: Employee) -> tuple[str, str]:
    _save_person(request, person)
    return "person", ""


def _page(request, employee_id, *, here: str, save, template: str, context):
    """Общий ход трёх страниц: найти человека, принять форму, нарисовать страницу.

    Один на всех, а не три копии: в нём живут правила, которые обязаны быть
    одинаковыми на каждой странице человека, — 404 до проверки права, 403 словами
    на запись без права, коды отказа формы и перенаправление после записи.
    Разъехавшись, они открыли бы на одной из страниц то, что закрыто на соседней.
    """
    who, denied = _reader(request)
    if denied is not None:
        return denied
    # Человек ищется ДО проверки права (T173): чужой сотрудник обязан отвечать
    # 404 всем одинаково. Если бы право спрашивалось первым, читатель получал бы
    # на своего человека 200, а на чужого — 403, то есть узнавал бы о его
    # существовании ровно по коду ответа (D023).
    person = _employee_or_404(who, employee_id)
    may_manage = permissions.has(who, permissions.DIRECTORY_MANAGE)

    error, status = "", 200
    if request.method == "POST":
        # Правка — по праву, и отказ здесь громкий: 403 словами. База отвергла
        # бы запись и сама (`0130`), но человеку из её ошибки не понятно ничего.
        try:
            permissions.check(who, permissions.DIRECTORY_MANAGE)
        except permissions.PermissionRefused as refusal:
            return _refusal(request, refusal)
        try:
            # Запись целиком внутри `saving()`: отказ базы по ограничению
            # становится отказом формы, а не оборванным запросом (T136).
            with saving():
                saved, carried = save(request, who, person)
            # Перенаправление после записи: обновление страницы не сохраняет
            # второй раз. Что случилось, уезжает в адрес кодом, а не готовой
            # фразой: фраза в адресе не переводится и подставляется кем угодно.
            return redirect(
                reverse(here, args=[person.id]) + f"?saved={saved}{carried}"
            )
        # Код ответа отказа — из самого отказа (400 по вводу и ограничению, 409
        # по состоянию данных): «сохранено» и «отказано» не должны быть
        # неразличимы для того, кто смотрит на ответ, а не на разметку (T142).
        except ConstraintRefused as refused:
            error, status = refused.message, refused.http_status
        except BadInput as bad:
            error, status = bad.message, bad.http_status
        except directory.DirectoryRefused as refusal:
            error, status = refusal.message, refusal.http_status

    shown = {
        **_header(request, who, person, here=here, error=error, may_manage=may_manage),
        **context(who, person, may_manage),
    }
    return render(request, template, shown, status=status)


def _header(request, who, person: Employee, *, here: str, error: str, may_manage: bool) -> dict:
    """Шапка страницы человека: имя, ряд его страниц, итог записи или отказ."""
    titles = _page_titles()
    pages = [CARD, TERMS, UNITS]
    # Выплаты — данные расчёта, а не справочник (T166). Ссылки нет у того, кому
    # экран откажет, в том числе у управляющего точки: ссылка на отказ хуже
    # отсутствующей — человек уходит со своей страницы, чтобы прочитать запрет.
    if permissions.has(who, permissions.PAYRUN_CALCULATE):
        pages.append(PAY)
    return {
        "person": person,
        "back_url": reverse("directory-employees"),
        "pages": [
            {"title": titles[name], "url": reverse(name, args=[person.id]),
             "selected": name == here}
            for name in pages
        ],
        "pages_label": _("Страницы сотрудника"),
        "notice": _notice(request, who),
        "error": error,
        "may_manage": may_manage,
        # Слова вместо формы у того, кто читает (T173, D047): пропавшая без слов
        # форма читается как поломка продукта, а не как запрет (T072). Те же,
        # которыми ответит сам отказ на `POST`.
        "denied": (
            "" if may_manage else permissions.explain(who, permissions.DIRECTORY_MANAGE)
        ),
    }


def _notice(request, who) -> str:
    """Итог записи словами: код из адреса и признаки закрытого месяца."""
    notice = _saved_notices().get(request.GET.get("saved", ""), "")
    if request.GET.get("posted") == "1":
        # Набор точек заведён датой, которую уже разнесли по P&L (T210). Слова
        # тут другие, чем у условий найма: разницы по точкам не бывает вовсе.
        notice = " ".join(filter(None, [
            notice, directory.split_already_posted_notice(who.tenant_id),
        ]))
    if request.GET.get("retro") == "1":
        # Версия завелась с датой внутри утверждённого месяца (T121): человек
        # обязан узнать, что закрытый месяц остался прежним и где искать разницу.
        notice = " ".join(filter(None, [notice, directory.closed_month_notice(who.tenant_id)]))
    return notice


def _versions(who, person: Employee) -> list[EmploymentTerm]:
    """Версии условий найма человека, регистр которых видит роль, по дате."""
    return _visible_terms(who, list(
        EmploymentTerm.objects.filter(employee_id=person.id)
        .select_related("group", "unit")
        .order_by("valid_from")
    ))


# --- карточка -------------------------------------------------------------------


def _card_context(who, person: Employee, may_manage: bool) -> dict:
    versions = _versions(who, person)
    current = versions[-1] if versions else None
    shown = {
        # Что человеку начислится в ближайшем открытом месяце (issue #185).
        # Считает тот же движок, что и месяц, и ничего не записывает.
        #
        # Только тому, кто ведёт расчёт. Управляющий точки читает карточку ради
        # ставок своих людей, и на его экранах чтения не должно быть ни одной
        # суммы расчёта (T173, D047) — предпросчёт был бы ровно такой суммой.
        "ahead": (
            _preview_of(who, person)
            if permissions.has(who, permissions.PAYRUN_CALCULATE) else None
        ),
    }
    if not may_manage:
        return {**shown, "facts": _facts(who, person, current)}
    return {
        **shown,
        "person_fields": [
            *_person_fields(person),
            {"kind": "date", "name": "hired_at", "label": _("Принят"),
             "value": person.hired_at.isoformat() if person.hired_at else ""},
            # Увольнение — это дата, а не удаление, и сказать об этом надо здесь
            # же. Пустое поле «Уволен» без слов читается как «уволить нечем».
            # Строка не исчезает никуда — по ней считаются закрытые месяцы, в
            # которых человек работал, и вернуть его можно, очистив дату.
            {"kind": "date", "name": "dismissed_at", "label": _("Уволен"),
             "value": person.dismissed_at.isoformat() if person.dismissed_at else "",
             "help": _("Уволить — значит поставить дату. Карточка и история "
                       "остаются: месяцы, в которые человек работал, считаются "
                       "по ним. Очистить дату можно — увольнение обратимо.")},
        ],
    }


def _facts(who, person: Employee, current: EmploymentTerm | None) -> list[dict]:
    """Карточка на чтение (T173, D047): в какой группе и по какой ставке он считается.

    Сквозного ключа среди фактов нет намеренно — там JMBG, и нужен он тому, кто
    сводит загрузку табеля, а не точке.
    """
    preset = _preset_now(who)
    return [
        {"label": _("Группа"), "value": current.group.title if current else EMPTY},
        {"label": _("Точка"),
         "value": current.unit.code if current and current.unit else EMPTY},
        {"label": _("Ставка"),
         "value": exact(current.base_rate) if current else EMPTY, "num": True},
        {"label": _("Коэффициент"),
         "value": exact(current.coefficient) if current else EMPTY, "num": True},
        # Чем меряется работа — управляющему это нужно так же, как ставка: по
        # этому он понимает, что вводить в табель — часы или величину за месяц
        # (T164, D047). Действующее значение целиком, включая унаследованное от
        # группы: факты отвечают на «как считается сейчас».
        {"label": _("Чем меряется работа"),
         "value": (_measure_title(preset, _effective_measure(preset, current))
                   if current else EMPTY)},
        {"label": _("Принят"),
         "value": person.hired_at.isoformat() if person.hired_at else EMPTY},
        {"label": _("Уволен"),
         "value": person.dismissed_at.isoformat() if person.dismissed_at else EMPTY},
    ]


# --- условия найма ----------------------------------------------------------------


def _terms_context(who, person: Employee, may_manage: bool) -> dict:
    versions = _versions(who, person)
    current = versions[-1] if versions else None
    # Правила нужны обоим режимам: подписать меру работы словом («По часам»), а
    # не ключом, — иначе в истории стоял бы `fixed_amount`.
    preset = _preset_now(who)
    shown = {
        # Именованные надбавки (issue #189). Всем, кто видит человека: это часть
        # условий его работы, а не сумма расчёта. Срез по регистру делает база.
        "allowances": _allowances_of(person),
        "versions": [_version_row(preset, term) for term in versions],
    }
    if not may_manage:
        return shown
    return {
        **shown,
        # Что случится с утверждённой зарплатой, сказано ДО правки (T121).
        "closed_note": directory.closed_month_warning(who.tenant_id),
        "terms_fields": [
            {"kind": "date", "name": "valid_from", "label": _("Действует с"),
             "value": "", "required": True,
             "help": _("С этой даты действует новая версия. Прошлая закрывается "
                       "этим же днём и остаётся в истории.")},
            *_terms_fields(who, current),
        ],
    }


def _version_row(preset, term: EmploymentTerm) -> dict:
    return {
        "from": day(term.valid_from),
        "to": day(term.valid_to),
        "group": term.group.title,
        "unit": term.unit.code if term.unit else EMPTY,
        # Ставка и коэффициент — основания расчёта, а не деньги: как есть, без
        # округления до копеек (`format.exact`, T116).
        "rate": exact(term.base_rate),
        "coefficient": exact(term.coefficient),
        # Схема — словом из правил страны, а не ключом (T164).
        "scheme": _scheme_title(preset, term.scheme or term.group.scheme),
        # Мера версии — только своя, а не унаследованная (T164): мера группы
        # версионируется своими датами, и в июньской строке сегодняшнее правило
        # группы было бы неправдой. «Как у группы» — честный ответ.
        "measure": (
            _measure_title(preset, term.work_measure) if term.work_measure
            else _("как у группы")
        ),
        "ledger": ledger_title(term.ledger or term.group.ledger),
    }


# --- точки затрат -------------------------------------------------------------------


def _units_context(who, person: Employee, may_manage: bool) -> dict:
    versions = _versions(who, person)
    current = versions[-1] if versions else None
    shown = {
        # Точки, между которыми делятся затраты (T210, D055). Всем, кто видит
        # человека: чужие точки не покажутся никому — на таблице стоит
        # `unit_visibility` (`0267`), тот же, что на условиях найма.
        "units": _unit_rows(person),
        # Точка из условий найма — для пустого состояния: набора нет, и деньги
        # идут не «никуда», а именно на неё (`payrun.posting._shares`).
        "units_home": current.unit.code if current and current.unit else "",
    }
    if not may_manage:
        return shown
    return {
        **shown,
        "unit_options": _unit_options(who, person),
        "network_option": _network_option(who, person),
        # Свои слова о разнесённом месяце, не `closed_note`: у точек закрытый
        # месяц ведёт себя иначе — разницы по ним не бывает вовсе.
        "units_posted_note": directory.split_already_posted_warning(who.tenant_id),
    }
