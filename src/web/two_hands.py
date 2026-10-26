"""Разбор первички в две руки (T206, issue #174, модуль эталона 15).

Эталон, «Путь накладной»: операционный директор говорит, чья накладная и за
что; бухгалтер видит её уже разнесённой и сверяет сумму и НДС; в раскрытии
строки P&L видно, кто разнёс и кто подтвердил.

**Кто какой шаг делает — право формы роли, а не имя роли.** Разнесение —
`suppliers.classify`, сверка — `suppliers.confirm`. Имена ролей здесь не
встречаются ни разу: «чьего решения ждёт строка» выводится из того, у каких
ролей партнёра есть право шага (D060 — доступы настраиваемые). Разведи партнёр
шаги по разным ролям экраном ролей — очередь заговорит его словами сама.

**Две руки держит база** (`0277`): сверяет не тот, кто разнёс, сверить можно
только разнесённое и только действующую версию, автор ставится умолчанием, а
история не переписывается правами таблицы. Проверки здесь — только чтобы
отказать словами раньше, чем база откажет кодом.
"""
from __future__ import annotations

from django.db import Error as DatabaseError
from django.db import connection, transaction
from django.db.models import BooleanField, Exists, OuterRef, Value
from django.db.models.expressions import RawSQL
from django.db.models.functions import Concat
from django.utils.translation import gettext as _

from core.models import Fact, Role

from . import cash, permissions
from .roles_display import shown_role_title

ALLOCATED = "allocated"
CONFIRMED = "confirmed"


class ReviewRefused(Exception):
    """Шаг не записан. Сообщение показывается человеку как есть."""

    http_status = 403

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


# --- запись шагов -------------------------------------------------------------


def _insert(fact_id, step: str) -> bool:
    """Записать шаг. `False` — база отказала: права, версия или чужая рука.

    Точка сохранения обязательна: запрос идёт одной транзакцией
    (`ATOMIC_REQUESTS`), и отказ политики без неё оборвал бы её целиком.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """insert into fact_reviews (tenant_id, fact_id, fact_key, step)
                   select tenant_id, id, dedup_key, %s from facts where id = %s
                   on conflict (fact_id, step) do nothing""",
                [step, str(fact_id)],
            )
            return True
    except DatabaseError:
        return False


def record_allocation(who, fact_id) -> None:
    """Первая рука: строке сказали, чья она и за что. Пишется вместе с разбором.

    Отказ здесь — не удобство, которое можно пропустить: без следа строка не
    попадёт к сверке, и вторая рука пропадёт молча. Поэтому он роняет разбор
    целиком (разбор и след — одна транзакция).
    """
    if not _insert(fact_id, ALLOCATED):
        raise cash.CashRefused(_(
            "Разбор не записан: разносить первичку — право роли «%(right)s», "
            "а у вашей роли его нет."
        ) % {"right": permissions.title(permissions.SUPPLIERS_CLASSIFY)})


def last_allocator(fact) -> str | None:
    """Кто разнёс строку последним — по ключу строки, а не по версии."""
    with connection.cursor() as cursor:
        # Та же функция, по которой решает политика `0277`: два ответа на вопрос
        # «кто разнёс» разошлись бы молча.
        cursor.execute(
            "select app_last_allocator(%s, %s)::text",
            [str(fact.tenant_id), fact.dedup_key],
        )
        return cursor.fetchone()[0]


def confirm(who, fact) -> None:
    """Вторая рука: сумма и НДС сверены. Отказ — словами, до базы."""
    try:
        permissions.check(who, permissions.SUPPLIERS_CONFIRM)
    except permissions.PermissionRefused as refusal:
        raise ReviewRefused(refusal.message) from refusal
    if last_allocator(fact) == str(who.user_id):
        raise ReviewRefused(_(
            "Эту строку разнесли вы. Сверяет сумму и НДС другой человек — "
            "в этом смысл двух рук."
        ))
    if not _insert(fact.id, CONFIRMED):
        raise ReviewRefused(_(
            "Сверка не записана: строка изменилась или ещё не разнесена. "
            "Обновите очередь."
        ))


# --- очередь сверки -----------------------------------------------------------


def _reviewed(step: str, *, by_key: bool) -> RawSQL:
    """Есть ли у строки шаг — по ключу строки или по самой версии.

    Таблицы `fact_reviews` в моделях нет намеренно: она служебная и живёт
    политиками базы. Условие — подзапросом, и отбор всё равно идёт под RLS.
    """
    if by_key:
        sql = ("exists (select 1 from fact_reviews r where r.tenant_id = facts.tenant_id"
               " and r.fact_key = facts.dedup_key and r.step = %s)")
    else:
        sql = "exists (select 1 from fact_reviews r where r.fact_id = facts.id and r.step = %s)"
    return RawSQL(sql, [step], output_field=BooleanField())


def awaiting_check():
    """Строки, которые разнесены и ждут сверки.

    Действующая версия, не сторно и не доля разнесения: доли сверяются вместе
    со своей строкой, а сторно — отмена, а не трата. Разнесена — по ключу
    (разбор мог заменить версию), не сверена — именно эта версия.
    """
    stornoed = Fact.objects.filter(
        tenant_id=OuterRef("tenant_id"),
        dedup_key=Concat(OuterRef("dedup_key"), Value(cash.STORNO_SUFFIX)),
        superseded_at__isnull=True,
    )
    return (
        Fact.objects.filter(superseded_at__isnull=True)
        .filter(_reviewed(ALLOCATED, by_key=True))
        .exclude(_reviewed(CONFIRMED, by_key=False))
        .exclude(allocation="allocated")
        .exclude(dedup_key__endswith=cash.STORNO_SUFFIX)
        .exclude(Exists(stornoed))
    )


def check_count(who) -> int:
    """Сколько строк ждёт сверки — для счётчика и ссылки из инбокса."""
    if who is None or who.tenant_id is None:
        return 0
    return awaiting_check().count()


# --- чьего решения ждёт -------------------------------------------------------


def deciders(tenant_id, code: str) -> str:
    """Роли партнёра, у которых есть право шага, — через запятую.

    Из базы, а не из умолчаний продукта: партнёр мог переназначить шаги экраном
    ролей, и очередь обязана говорить его словами. Ни у кого нет — так и
    сказано: строка, которую некому разобрать, иначе ждала бы молча.
    """
    roles = (
        Role.objects.filter(tenant_id=tenant_id, permissions__contains=[code])
        .order_by("title")
    )
    titles = [shown_role_title(role.title, role.code) for role in roles]
    return ", ".join(titles) if titles else _("ни у одной роли нет этого права")


# --- раскрытие P&L ------------------------------------------------------------


def hands_of(fact_ids) -> dict[str, tuple[str, str]]:
    """Кто разнёс и кто подтвердил — по номеру строки отчёта.

    Строка отчёта может быть долей разнесения: тогда след берётся у её
    родителя, разнесённой строки целиком. Разнёс — последнее разнесение по
    ключу; подтвердил — сверка именно этой версии. Имена — снимком из самого
    следа (`0277`): учётки людей открыты только тому, кто ведёт роли.

    Строке, внесённой сразу со статьёй, следа нет: второй руки ей не положено,
    и «ждёт сверки» про неё было бы неправдой — там пусто.
    """
    if not fact_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            with line as (
                select l.id, coalesce(p.id, l.id) as head_id,
                       coalesce(p.dedup_key, l.dedup_key) as head_key, l.tenant_id
                  from facts l
                  left join facts p on p.id = l.parent_fact_id
                 where l.id = any(%s::uuid[])
            )
            select line.id::text, alloc.done_by_name, conf.done_by_name
              from line
              left join lateral (
                    select r.done_by_name from fact_reviews r
                     where r.tenant_id = line.tenant_id and r.fact_key = line.head_key
                       and r.step = 'allocated'
                     order by r.done_at desc, r.id desc limit 1) alloc on true
              left join fact_reviews conf
                     on conf.fact_id = line.head_id and conf.step = 'confirmed'
            """,
            [[str(fact_id) for fact_id in fact_ids]],
        )
        rows = cursor.fetchall()
    hands = {}
    for fact_id, allocator, confirmer in rows:
        if allocator is None:
            continue
        hands[fact_id] = (allocator, confirmer or _("ждёт сверки"))
    return hands
