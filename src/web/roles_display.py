"""Как роли показываются человеку: название на языке страницы (T233, issue #283).

Названия ролей лежат в базе по-русски — так их кладёт заведение партнёра
(`core.roles.DEFAULT_TITLES`). На английской и сербской страницах экран ролей
показывал «Бухгалтер» посреди перевода. Переводить данные партнёра нельзя
(`web/i18n`), но нетронутое название — не данные партнёра, а слово продукта,
которое продукт сам туда и положил.

Отсюда правило: название, совпадающее с исходным для своей роли, показывается
переводом (`web.i18n.role_title`); переименованное партнёром — как он его
назвал. Снимок названия в истории доступов (`access_log.role_title`) читается
так же: кода роли у записи нет, поэтому роль узнаётся по исходному названию.
"""
from __future__ import annotations

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from core.roles import ALL_LEDGERS, ALL_PERMISSIONS, DEFAULT_TITLES, NEVER, OPTIONAL

from . import permissions
from .format import ledger_title
from .i18n import role_title

_CODE_BY_DEFAULT = {title: code for code, title in DEFAULT_TITLES.items()}


def shown_role_title(title: str, code: str = "") -> str:
    """Название роли для экрана: перевод, если партнёр его не менял."""
    title = (title or "").strip()
    code = code or _CODE_BY_DEFAULT.get(title, "")
    if code and DEFAULT_TITLES.get(code) == title:
        return role_title(code, title)
    return title


def role_choice(role) -> dict:
    """Роль для шаблона: идентификатор, код и показываемое название."""
    return {"id": role.pk, "code": role.code, "title": shown_role_title(role.title, role.code)}


# Группы матрицы прав — как в эталоне «Роли и права»: строки сетки идут не
# списком кодов, а по участкам работы, чтобы человек искал право там, где его
# и ждёт. Регистры — отдельная группа между деньгами и правилами, как в эталоне;
# их строки только на чтение (`LEDGER_GROUP`).
RIGHT_GROUPS = (
    (gettext_noop("Табель и операционка"),
     ("timesheet.edit", "unit.close", "suppliers.classify")),
    (gettext_noop("Зарплата и деньги"),
     ("payrun.calculate", "period.approve", "period.reopen", "payslip.freeze", "retro.post")),
    (gettext_noop("Правила и справочники"), ("rules.manage", "directory.manage")),
    (gettext_noop("Доступ и история"), ("roles.manage",)),
)
LEDGER_GROUP = gettext_noop("Срезы по регистрам учёта")
LEDGER_GROUP_AFTER = 1  # после «Зарплата и деньги», как в эталоне

# Право, которое забыли положить в группу, пропало бы с экрана молча — и его
# стало бы нечем выдать. Так уже было с `suppliers.classify` (T203). Поэтому
# расхождение роняет импорт, а не рисует неполную сетку.
_GROUPED = [code for _title, codes in RIGHT_GROUPS for code in codes]
if sorted(_GROUPED) != sorted(ALL_PERMISSIONS):
    raise RuntimeError(
        "группы матрицы прав разошлись с core.roles.ALL_PERMISSIONS: "
        f"{sorted(set(ALL_PERMISSIONS) ^ set(_GROUPED))}"
    )

# Сокращение роли для столбца сетки. На 390 px в столбец помещается три
# буквы, а не «Оперативный директор»; полное название — в подсказке и легенде.
_SHORT = {
    "director": gettext_noop("ДИР"),
    "accountant": gettext_noop("БУХ"),
    "manager": gettext_noop("УПР"),
    "admin": gettext_noop("АДМ"),
}
_SHORT_LETTERS = 3


def role_short(code: str, title: str) -> str:
    """Сокращение роли: своё у ролей продукта, у роли партнёра — первые буквы."""
    known = _SHORT.get(code)
    if known is not None:
        return _(known)
    return (title or code)[:_SHORT_LETTERS].upper()


def _right_row(code: str, columns: list[dict], roles: list) -> dict:
    return {
        "code": code,
        "title": permissions.title(code),
        "cells": [
            {
                "role": column,
                "granted": code in (role.permissions or []),
                # Стена рисуется прочерком, а не пустой галочкой: иначе она
                # выглядит как «просто не выдано», человек её жмёт и получает
                # отказ на то, что экран сам ему и предложил.
                "walled": (role.permission_states or {}).get(code, OPTIONAL) == NEVER,
            }
            for column, role in zip(columns, roles, strict=True)
        ],
    }


def _ledger_row(ledger: str, columns: list[dict], roles: list) -> dict:
    return {
        "code": ledger,
        "title": ledger_title(ledger),
        "ledger": True,
        "cells": [
            {"role": column, "granted": ledger in (role.visible_ledgers or [])}
            for column, role in zip(columns, roles, strict=True)
        ],
    }


# Столбцы — от самой широкой роли к самой узкой, как в эталоне (АДМ первым):
# так столбец администратора, заполненный целиком, читается как точка отсчёта.
# Роли, заведённые партнёром, идут следом по названию.
_COLUMN_ORDER = ("admin", "director", "accountant", "manager")


def _column_key(role) -> tuple:
    if role.code in _COLUMN_ORDER:
        return (_COLUMN_ORDER.index(role.code), "")
    return (len(_COLUMN_ORDER), role.title or "")


def rights_matrix(roles: list) -> dict:
    """Сетка «право × роль» для экрана прав: столбцы и группы строк."""
    roles = sorted(roles, key=_column_key)
    columns = [
        {**role_choice(role), "short": role_short(role.code, role.title)} for role in roles
    ]
    groups = [
        {"title": _(title), "rows": [_right_row(code, columns, roles) for code in codes]}
        for title, codes in RIGHT_GROUPS
    ]
    ledgers = {
        "title": _(LEDGER_GROUP),
        "rows": [_ledger_row(ledger, columns, roles) for ledger in ALL_LEDGERS],
    }
    groups.insert(LEDGER_GROUP_AFTER + 1, ledgers)
    return {"columns": columns, "groups": groups}
