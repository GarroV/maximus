"""Выгрузка истории доступов в CSV (T233, issue #283).

Экран истории показывает последние записи; разговор «почему у него были эти
права в июне» требует всех. Поэтому выгрузка — все записи партнёра, без
обрезки, в том же порядке, что на экране (новые сверху).

Файл собирается целиком в памяти, а не потоком: контекст пользователя в базе
живёт внутри транзакции запроса (`ATOMIC_REQUESTS`), и поток, дочитываемый
после выхода из представления, читал бы уже без него — политики `access_log`
отдали бы пустоту, и файл вышел бы пустым молча.

Кодировка — UTF-8 с меткой порядка байт: без неё Excel открывает кириллицу и
сербскую латиницу с диакритикой мусором.
"""
from __future__ import annotations

import csv
import io
from datetime import date

from django.db import connection
from django.http import HttpResponse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from .roles_display import shown_role_title

# Слова действий — существительными, а не глаголом из фразы экрана: в таблице
# у действия свой столбец, и «выдал» без подлежащего читается обрывком.
_ACTIONS = {
    "invited": gettext_noop("приглашение"),
    "granted": gettext_noop("выдача роли"),
    "revoked": gettext_noop("снятие роли"),
}

# Ячейка, начатая с этих знаков, в Excel и его родне — формула, а не текст.
# Причину пишет человек, и «=HYPERLINK(...)» в ней сработало бы у того, кто
# откроет файл. Такая ячейка отдаётся с апострофом впереди: таблица покажет
# текст как есть.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _rows(tenant_id):
    with connection.cursor() as cur:
        cur.execute(
            """
            select to_char(l.at, 'YYYY-MM-DD HH24:MI'),
                   coalesce(nullif(a.full_name, ''), a.username, '—'),
                   coalesce(nullif(s.full_name, ''), s.username, '—'),
                   l.action, l.role_title,
                   coalesce(to_char(l.until, 'YYYY-MM-DD'), ''),
                   l.reason
              from access_log l
              left join users a on a.id = l.actor_user_id
              left join users s on s.id = l.subject_user_id
             where l.tenant_id = %s
             order by l.at desc
            """,
            [tenant_id],
        )
        return cur.fetchall()


def history_csv(tenant_id) -> HttpResponse:
    """Ответ с файлом: все записи истории доступов партнёра."""
    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out)
    writer.writerow([_("Когда"), _("Кто"), _("Кому"), _("Действие"), _("Роль"),
                     _("До какого числа"), _("Зачем")])
    for at, actor, subject, action, role, until, reason in _rows(tenant_id):
        known = _ACTIONS.get(action)
        writer.writerow([_cell(value) for value in (
            at, actor, subject, _(known) if known else action,
            shown_role_title(role), until, reason,
        )])
    response = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
    # Имя латиницей — по тому же доводу, что у выгрузок отчётов (`reports_views`).
    response["Content-Disposition"] = (
        f'attachment; filename="access-history-{date.today().isoformat()}.csv"'
    )
    return response
