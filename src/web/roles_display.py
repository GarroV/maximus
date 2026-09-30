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

from core.roles import DEFAULT_TITLES

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
