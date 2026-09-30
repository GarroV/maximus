"""Что человек получит и что потеряет при смене роли (T233, issue #283).

Прежде «Снять роль» и «Выдать» не говорили, чем это обернётся: права роли
видны были только на соседней странице, а сложить их с остальными ролями
человека приходилось в уме. Ошибка здесь тихая — администратор снимает роль,
уверенный, что права остаются в другой, а человек утром не может закрыть месяц.

Считается по тем же полям, по которым считает база: выданные права роли
(`roles.permissions`) и видимые регистры (`roles.visible_ledgers`). Несколько
ролей у человека складываются (D047), поэтому потеря — это то, чего нет ни в
одной из ОСТАЛЬНЫХ его ролей, а приобретение — то, чего нет ни в одной из
уже выданных.
"""
from __future__ import annotations

from django.utils.translation import gettext as _

from core.roles import ALL_LEDGERS, ALL_PERMISSIONS

from . import permissions
from .format import ledger_title
from .roles_display import shown_role_title


def _abilities(role) -> set[str]:
    rights = {f"right:{code}" for code in (role.permissions or [])}
    ledgers = {f"ledger:{ledger}" for ledger in (role.visible_ledgers or [])}
    return rights | ledgers


# Порядок слов на экране — порядок продукта, а не порядок множества: тот же,
# что у строк матрицы прав.
_ORDER = [f"right:{code}" for code in ALL_PERMISSIONS] + [
    f"ledger:{ledger}" for ledger in ALL_LEDGERS
]


def _words(abilities: set[str]) -> list[str]:
    words = []
    for key in _ORDER:
        if key not in abilities:
            continue
        kind, code = key.split(":", 1)
        if kind == "right":
            words.append(permissions.title(code))
        else:
            words.append(_("регистр «%(ledger)s»") % {"ledger": ledger_title(code)})
    return words


def role_effects(held_ids: list[str], roles: list) -> dict:
    """Потери по каждой выданной роли и приобретения по каждой невыданной.

    `held_ids` — роли человека (строками), `roles` — все роли партнёра.
    """
    by_id = {str(role.pk): role for role in roles}
    held = [by_id[rid] for rid in held_ids if rid in by_id]
    has = set().union(*(_abilities(role) for role in held)) if held else set()
    loses = {}
    for role in held:
        others = [other for other in held if other.pk != role.pk]
        kept = set().union(*(_abilities(other) for other in others)) if others else set()
        loses[str(role.pk)] = _words(_abilities(role) - kept)
    offers = [
        {"id": role.pk, "title": shown_role_title(role.title, role.code),
         "gains": _words(_abilities(role) - has)}
        for role in roles if str(role.pk) not in {str(h.pk) for h in held}
    ]
    return {"now": _words(has), "loses": loses, "offers": offers}
