"""Отбор списков ссылками, а не выпадающими списками (T224, D081).

Владелец: «никаких выпадающих списков». Отбор, спрятанный в `<select>` с кнопкой
«Показать», прячет и сами варианты: человек не видит, какие точки и регистры у
него вообще есть, пока не раскроет список, и делает лишний шаг ради каждого
переключения. Здесь каждое значение отбора — видимая ссылка, выбранное — не
ссылка вовсе (`span[aria-current]`), как у разрезов ведомости и аналитики по
людям.

**Состояние экрана целиком в адресе.** Каждая ссылка несёт весь остальной отбор:
поменять точку, не потеряв регистр и месяц, — это и есть «фильтры
комбинируются». Разбор адреса остаётся прежним (`filters_from` у каждого
экрана): адрес, который строит эта ссылка, обязан разбираться тем же кодом, что
и адрес, набранный руками или присланный ботом.

**Варианты — только из видимого роли** (D023). Модуль вариантов не собирает:
ему приносят готовые списки, собранные экраном из того, что роль видит, — так
же, как компоненту `cuts.html`.

**Где значений много, ряда нет.** Статей расхода и контрагентов десятки; ряд из
сорока ссылок — тот же выпадающий список, только развёрнутый на полэкрана.
Там на экране стоит выбранное значение и ссылка «выбрать…» на отдельную
страницу выбора с поиском: одна страница — одна функция (D081).
"""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode

from .i18n import month_title


def query_of(chosen: dict, default: dict) -> dict:
    """Отбор как параметры адреса: только то, что отличается от умолчания.

    Умолчание в адрес не пишется: `/expenses/` и `/expenses/?from=…&to=…` за
    текущий месяц — один и тот же экран, и ссылка на него должна быть одной.
    """
    query = {}
    for name, value in chosen.items():
        if value == default.get(name):
            continue
        query[name] = value.isoformat() if isinstance(value, date) else str(value)
    return query


def url(base: str, query: dict, **change) -> str:
    """Адрес экрана с отбором `query`, в котором поменяно `change`.

    Пустое значение в `change` снимает параметр: «Все точки» — это адрес без
    `unit`, а не `unit=` (пустой номер разбор отверг бы как не номер).
    """
    merged = {**query, **{k: v for k, v in change.items()}}
    shown = {k: v for k, v in merged.items() if v not in ("", None)}
    return f"{base}?{urlencode(shown)}" if shown else base


def row(name: str, label, options, current: str, *, base: str, query: dict,
        everything=None) -> dict:
    """Один ряд переключателя: подпись и ссылки, выбранная — без ссылки.

    `options` — пары (значение, подпись) из видимого роли. `everything` — подпись
    варианта «без отбора» (значение — пустое); `None` — такого варианта нет.

    Ключ — `choices`, а не `items`: шаблон Django читает `f.items` как метод
    словаря `dict.items`, и ряд рисовался бы пустыми ссылками без единой ошибки.
    """
    pairs = ([("", everything)] if everything is not None else []) + [
        (str(value), title) for value, title in options
    ]
    return {
        "name": name,
        "label": label,
        "choices": [
            {"title": title, "selected": value == str(current or ""),
             "url": url(base, query, **{name: value})}
            for value, title in pairs
        ],
    }


def month_bounds(first: date) -> tuple[date, date]:
    first = first.replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return first, last


def months(chosen: dict, *, base: str, query: dict) -> dict:
    """Период — соседние месяцы ссылками, выбранный — без ссылки.

    Касса и счета сверяются помесячно, поэтому переключатель — месяцами, а не
    двумя полями дат с кнопкой. Произвольный диапазон по-прежнему разбирается из
    адреса (его строят выгрузки и вызов по HTTP); тогда выбранным показан сам
    диапазон, а соседние месяцы считаются от его начала.
    """
    start, end = chosen["from"], chosen["to"]
    first, last = month_bounds(start)
    whole = (start, end) == (first, last)
    before = month_bounds(first - timedelta(days=1))
    after = month_bounds(last + timedelta(days=1))
    current = month_title(first) if whole else f"{_short(start)} — {_short(end)}"

    def link(bounds):
        return url(base, query, **{"from": bounds[0].isoformat(), "to": bounds[1].isoformat()})

    return {
        "name": "period",
        "choices": [
            {"title": month_title(before[0]), "selected": False,
             "url": link(before), "rel": "prev"},
            {"title": current, "selected": True, "url": "", "rel": ""},
            {"title": month_title(after[0]), "selected": False, "url": link(after), "rel": "next"},
        ],
    }


def _short(day: date) -> str:
    from django.utils import formats

    return formats.date_format(day, "SHORT_DATE_FORMAT")


def picked(name: str, label, title: str, current: str, *, base: str, query: dict,
           pick_url: str, everything) -> dict:
    """Отбор с большим числом значений: выбранное словами и «выбрать…».

    `title` — подпись выбранного значения. Номер, которого роль не видит, и
    номер, которого нет вовсе, приезжают одинаковой подписью «не найдено» и
    одинаково дают пустой список (D023): экран не отличает чужое от несуществующего.
    """
    return {
        "name": name,
        "label": label,
        "current": title if current else everything,
        "is_set": bool(current),
        "reset_url": url(base, query, **{name: ""}),
        "everything": everything,
        "pick_url": url(pick_url, query),
    }


def pick_page(options, current: str, *, name: str, q: str, base: str, query: dict,
              title, everything, searched: bool = False) -> dict:
    """Контекст страницы выбора: варианты ссылками, поиск куском названия.

    `options` — пары (значение, подпись) из видимого роли. Поиск идёт по уже
    собранным подписям на языке страницы: ищут то, что видят, а не код в базе.
    `searched` — варианты уже отобраны по `q` на стороне экрана (контрагента
    ищут и по налоговому номеру, и по написанию из выписки — это умеет только
    выборка из базы).
    Вариант «все» стоит первым всегда — снять отбор так же легко, как поставить.
    """
    needle = q.casefold()
    found = [(value, label) for value, label in options
             if searched or not needle or needle in str(label).casefold()]
    # Скрытые поля поиска несут остальной отбор, кроме того, что выбирается здесь:
    # поиск не должен терять месяц и регистр, пока человек ищет статью.
    keep = {k: v for k, v in query.items() if k != name}
    return {
        "title": title,
        "back_url": url(base, query),
        "q": q,
        "keep": sorted(keep.items()),
        # «Сбросить поиск» — эта же страница без `q`, с тем же остальным отбором.
        "keep_query": urlencode(sorted(keep.items())),
        "everything": {"title": everything, "selected": not current,
                       "url": url(base, query, **{name: ""})},
        "options": [
            {"title": label, "selected": value == current,
             "url": url(base, query, **{name: value})}
            for value, label in found
        ],
    }


def reset(base: str, query: dict) -> str:
    """Снять весь отбор, кроме месяца: «показать весь месяц» — тот, что открыт.

    Раньше ссылка вела на голый адрес, то есть на текущий месяц: человек,
    смотревший май, после «показать весь месяц» оказывался в сентябре.
    """
    return url(base, {k: v for k, v in query.items() if k in ("from", "to")})
