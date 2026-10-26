"""Банковская выписка в данных: загрузка, сопоставление, решения по строке (T198).

Разбор файла, ключ строки и правило сопоставления — в пакете `bankstatement`
(без базы). Здесь — то, что требует базы: что уже загружено, какие платежи и
счета внесены, и запись решения.

## Что делает загрузка

1. Тот же файл второй раз — отказ с отсылкой к первой загрузке: не заводится
   ни выписка, ни строки (`bank_statements_same_file_once`).
2. Строка, чей ключ уже есть у партнёра, ложится **дублем** со ссылкой на
   исходную: в учёт не идёт, остаётся видимой (эталон, модуль 7).
3. Новые строки сводятся с внесённым по `bankstatement.matching`: однозначная
   пара — сама, остальное ждёт человека.

## Чего здесь нет и почему

Своего `insert` в `facts` нет: оплата счёта идёт через `suppliers.pay`, строка
в инбокс — через `cash.write_fact`. Идемпотентность по ключу, защита закрытого
месяца и перевод отказа базы в слова живут там, и второй путь записи обошёл бы
их все.

Неразобранная строка выписки **в P&L не попадает сама** (эталон, модули 4 и 5:
«4 платежа выписки не сведены» стоит над отчётом отдельно). Строка банка без
решения — ещё не трата: это может быть оплата внесённого счёта, зарплата,
перевод между своими счетами. Записать её расходом до решения значило бы
удвоить уже признанное. В учёт она идёт решением человека — оплатой счёта или
строкой в инбоксе «Не разобрано» (D076).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Value
from django.db.models.functions import Concat
from django.utils import timezone
from django.utils.translation import gettext as _

from bankstatement.keys import line_keys
from bankstatement.matching import DATE_WINDOW_DAYS, FACT, INVOICE, Candidate, choose
from bankstatement.matching import DECIDE as DECIDE_KIND
from bankstatement.model import OUT
from bankstatement.reader import read
from bankstatement.values import norm
from core.models import BankLine, BankLineLink, BankStatement, Counterparty, Fact

from . import cash, suppliers

# Состояния строки — выводятся, а не хранятся (см. `BankLine`).
MATCHED = "matched"
DECIDE = "decide"
DUPLICATE = "duplicate"
NOT_OURS = "not_ours"

AUTO, MANUAL, PAID, INBOX = "auto", "manual", "paid", "inbox"
BANK_SOURCE = "bank"
INBOX_PREFIX = "bank:line:"
# Окно, в котором человеку предлагаются платежи той же суммы. Шире окна
# автосопоставления: сам выбор делает человек, и платёж, внесённый задним
# числом через две недели, он узнает, а правило — не должно.
SHOWN_WINDOW_DAYS = 31


class BankRefused(cash.CashRefused):
    """Решение по строке не принято. Текст показывается человеку как есть."""


class AlreadyLoaded(BankRefused):
    def __init__(self, statement: BankStatement):
        self.statement = statement
        super().__init__(_(
            "Этот файл уже загружен %(when)s — второй раз он ничего не добавит."
        ) % {"when": timezone.localtime(statement.created_at).strftime("%d.%m.%Y %H:%M")})


@dataclass
class Loaded:
    statement: BankStatement
    counts: dict = field(default_factory=dict)


# --- загрузка -----------------------------------------------------------------


def upload(who, content: bytes, file_name: str) -> Loaded:
    """Загрузить файл выписки. Отказ разбора — `bankstatement.reader.Unreadable`."""
    parsed = read(content, file_name)
    content_hash = hashlib.sha256(content).hexdigest()
    earlier = BankStatement.objects.filter(
        tenant_id=who.tenant_id, content_hash=content_hash,
    ).first()
    if earlier is not None:
        raise AlreadyLoaded(earlier)
    keys = line_keys(parsed.account, parsed.lines)
    with transaction.atomic():
        statement = BankStatement.objects.create(
            tenant_id=who.tenant_id, account=parsed.account, bank=parsed.bank,
            currency=parsed.currency, file_name=file_name[:200], format=parsed.format,
            content_hash=content_hash, period_from=parsed.period_from,
            period_to=parsed.period_to, uploaded_by=who.user_id,
            stats={"mapping": parsed.mapping},
        )
        originals = dict(
            BankLine.objects.filter(tenant_id=who.tenant_id, dedup_key__in=keys,
                                    duplicate_of__isnull=True)
            .values_list("dedup_key", "id")
        )
        parties = PartyIndex(who.tenant_id)
        created = []
        for number, (line, key) in enumerate(zip(parsed.lines, keys, strict=True), start=1):
            created.append(BankLine.objects.create(
                tenant_id=who.tenant_id, statement=statement, line_no=number,
                op_date=line.op_date, direction=line.direction, amount=line.amount,
                currency=line.currency or parsed.currency, party_name=line.party_name,
                party_account=line.party_account, purpose=line.purpose,
                bank_ref=line.bank_ref, dedup_key=key,
                duplicate_of_id=originals.get(key),
                counterparty_id=parties.find(line.party_name),
            ))
        fresh = [row for row in created if row.duplicate_of_id is None]
        auto_match(who, fresh)
        counts = count_states(statement_lines(statement))
        statement.stats = {"mapping": parsed.mapping, "counts": counts}
        statement.save(update_fields=["stats"])
    return Loaded(statement=statement, counts=counts)


class PartyIndex:
    """Контрагент по написанию в выписке: название или одно из `aliases`.

    Сравнение без регистра, пробелов и знаков: «KOMERC PROMET DOO» и
    «Komerc Promet d.o.o.» — одно. Совпадение у двух контрагентов — не
    угадываем, оставляем пустым: лучше «нужно решить», чем чужой поставщик.
    """

    def __init__(self, tenant_id):
        found: dict[str, set] = {}
        for party in Counterparty.objects.filter(tenant_id=tenant_id, valid_to__isnull=True):
            for name in [party.title, *(party.aliases or [])]:
                found.setdefault(norm(name), set()).add(party.id)
        self._by_name = {name: ids.pop() for name, ids in found.items()
                         if name and len(ids) == 1}

    def find(self, name: str):
        return self._by_name.get(norm(name))


# --- состояние строк ------------------------------------------------------------


def _alive_links():
    """Связи, за которыми стоит живой факт: исходный без сторно или исправление.

    Разбор и правка заменяют версию факта с тем же ключом, правка закрытого
    месяца кладёт сторно и `#fix` рядом (D020). Связь жива, пока по ключу
    есть действующая учётная строка; удалённый платёж обрывает её, и строка
    выписки честно возвращается в «нужно решить».
    """
    def active(suffix: str = ""):
        key = Concat(OuterRef("fact_key"), Value(suffix)) if suffix else OuterRef("fact_key")
        return Fact.objects.filter(
            tenant_id=OuterRef("tenant_id"), dedup_key=key, superseded_at__isnull=True,
        )

    return BankLineLink.objects.filter(line_id=OuterRef("pk")).filter(
        Q(Exists(active(cash.FIX_SUFFIX)))
        | (Q(Exists(active())) & ~Q(Exists(active(cash.STORNO_SUFFIX))))
    )


def with_state(rows):
    return rows.annotate(matched=Exists(_alive_links()))


def state_of(row) -> str:
    if row.duplicate_of_id is not None:
        return DUPLICATE
    if row.not_ours_at is not None:
        return NOT_OURS
    return MATCHED if row.matched else DECIDE


def statement_lines(statement):
    return list(with_state(
        BankLine.objects.filter(statement=statement).select_related("counterparty")
    ).order_by("line_no"))


def count_states(rows) -> dict:
    counts = {MATCHED: 0, DECIDE: 0, DUPLICATE: 0, NOT_OURS: 0}
    for row in rows:
        counts[state_of(row)] += 1
    return counts


def waiting_count(who) -> int:
    """Сколько строк ждут решения — для левой панели и инбокса."""
    if who is None or getattr(who, "tenant_id", None) is None:
        return 0
    return with_state(BankLine.objects.filter(
        tenant_id=who.tenant_id, duplicate_of__isnull=True, not_ours_at__isnull=True,
    )).filter(matched=False).count()


def line_or_none(who, line_id):
    """Строка по номеру — своего партнёра, под политиками базы.

    Партнёр — явным условием, а не только политикой: человек может состоять
    у нескольких партнёров, и строка другого из них не должна решаться из
    контекста этого. Чужая и выдуманная отвечают одинаково (D023).
    """
    return with_state(
        BankLine.objects.select_related("counterparty", "statement", "duplicate_of__statement")
    ).filter(tenant_id=who.tenant_id, pk=line_id).first()


# --- кандидаты ------------------------------------------------------------------


def _payment_facts():
    """Внесённые платежи по банку: оплаты счетов и оплаты без счёта."""
    stornoed = Fact.objects.filter(
        tenant_id=OuterRef("tenant_id"),
        dedup_key=Concat(OuterRef("dedup_key"), Value(cash.STORNO_SUFFIX)),
        superseded_at__isnull=True,
    )
    return (
        Fact.objects.filter(channel="bank", superseded_at__isnull=True)
        .filter(Q(dedup_key__startswith=suppliers.PAYMENT_PREFIX)
                | Q(dedup_key__startswith=suppliers.PURCHASE_PREFIX))
        .exclude(allocation="allocated")
        .exclude(dedup_key__endswith=cash.STORNO_SUFFIX)
        .exclude(Exists(stornoed))
        .select_related("counterparty", "document")
    )


def base_key(dedup_key: str) -> str:
    """Ключ события: исправление закрытого месяца — то же событие (D020)."""
    return dedup_key.removesuffix(cash.FIX_SUFFIX)


def _taken(tenant_id) -> set[str]:
    return set(BankLineLink.objects.filter(tenant_id=tenant_id)
               .values_list("fact_key", flat=True))


def payment_candidates(row, *, days: int) -> list[tuple[Candidate, Fact]]:
    """Свободные внесённые платежи той же суммы в окне дат.

    Приход сводится с отрицательным платежом (возврат поставщика): знак у
    строки выписки в направлении, у факта — в сумме.
    """
    signed = row.amount if row.direction == OUT else -row.amount
    taken = _taken(row.tenant_id)
    found = _payment_facts().filter(
        tenant_id=row.tenant_id, amount=signed,
        doc_date__gte=row.op_date - timedelta(days=days),
        doc_date__lte=row.op_date + timedelta(days=days),
    ).order_by("doc_date", "created_at")
    return [
        (Candidate(kind=FACT, ref=base_key(fact.dedup_key), amount=row.amount,
                   on=fact.doc_date, party=_id(fact.counterparty_id)), fact)
        for fact in found if base_key(fact.dedup_key) not in taken
    ]


def open_invoices(who) -> list[tuple[Candidate, object]]:
    """Счета с остатком к оплате: остаток = сумма счёта минус платежи."""
    listed = suppliers.invoices(who, {})
    paid = suppliers.paid_by_document([item.document.id for item in listed])
    found = []
    for item in listed:
        left = suppliers.outstanding(item.amount, paid.get(str(item.document.id)))
        if left > 0:
            found.append((Candidate(kind=INVOICE, ref=str(item.document.id), amount=left,
                                    on=item.document.doc_date,
                                    party=_id(item.document.counterparty_id)), item))
    return found


def _id(value) -> str | None:
    return str(value) if value is not None else None


# --- сопоставление ----------------------------------------------------------------


def auto_match(who, rows) -> int:
    """Свести то, что сводится однозначно. Возвращает, сколько свело."""
    if not rows:
        return 0
    invoices = [c for c, _item in open_invoices(who)]
    matched = 0
    for row in rows:
        facts = [c for c, _f in payment_candidates(row, days=DATE_WINDOW_DAYS)]
        payable = invoices if row.direction == OUT else []
        chosen = choose(row.amount, row.op_date, _id(row.counterparty_id), facts, payable)
        if chosen.kind == DECIDE_KIND:
            continue
        # Каждая попытка — своей точкой сохранения: отказ (месяц закрылся,
        # политика не пустила) оставляет строку ждать человека, а не роняет
        # всю загрузку.
        try:
            with transaction.atomic():
                if chosen.kind == FACT:
                    _link(who, row, chosen.ref, AUTO)
                else:
                    _pay(who, row, chosen.ref, how=AUTO)
        except cash.CashRefused:
            continue
        if chosen.kind == INVOICE:
            # Оплаченный счёт больше не кандидат: второй строкой его не оплатить.
            invoices = [c for c in invoices if c.ref != chosen.ref]
        matched += 1
    return matched


def _link(who, row, fact_key: str, how: str) -> None:
    BankLineLink.objects.create(
        tenant_id=who.tenant_id, line=row, fact_key=fact_key, how=how,
        linked_by=who.user_id,
    )


def _open(row) -> None:
    """Решать можно только строку, которая ждёт решения."""
    if row.duplicate_of_id is not None:
        raise BankRefused(_("Это дубль уже загруженной строки — в учёт он не идёт."))
    if row.not_ours_at is not None:
        raise BankRefused(_("Строка помечена как не наш платёж. Сначала верните её в работу."))
    if row.matched:
        raise BankRefused(_("Строка уже сопоставлена."))


def link(who, row, fact_key: str) -> None:
    """Человек выбрал внесённый платёж: та же сумма, свободный, в окне показа."""
    _open(row)
    offered = {c.ref for c, _f in payment_candidates(row, days=SHOWN_WINDOW_DAYS)}
    if fact_key not in offered:
        raise BankRefused(_(
            "Этот платёж не подходит: сумма другая, он уже сведён с другой строкой "
            "или вам не виден."
        ))
    with transaction.atomic():
        _link(who, row, fact_key, MANUAL)


def pay(who, row, document_id: str) -> None:
    """Строка выписки — оплата этого счёта: заводится платёж с датой банка."""
    _open(row)
    if row.direction != OUT:
        raise BankRefused(_("Поступление не оплачивает счёт поставщика."))
    with transaction.atomic():
        _pay(who, row, document_id, how=MANUAL)


def _pay(who, row, document_id: str, *, how: str) -> None:
    document = suppliers.document_or_none(document_id)
    # Счёт — того же партнёра, что строка и человек: явным условием, не только
    # политикой (см. `line_or_none`).
    if document is None or document.tenant_id != who.tenant_id:
        raise BankRefused(_("Счёт не найден."))
    left = suppliers.outstanding(
        suppliers.invoice_amount(document),
        suppliers.paid_by_document([document.id]).get(str(document.id)),
    )
    if row.amount > left:
        raise BankRefused(_(
            "Платёж больше остатка по счёту (%(left)s). Разложить один платёж на "
            "несколько счетов или провести остаток авансом пока нельзя — отправьте "
            "строку в инбокс или выберите другой счёт."
        ) % {"left": f"{left:.2f}"})
    primary = suppliers.invoice_fact(document)
    entry_key = f"bank-{row.id}"
    suppliers.pay(
        who, document, entry_key=entry_key, on=row.op_date, amount=row.amount,
        till_id=None, ledger=primary.ledger if primary else "official",
        note=row.purpose[:500],
    )
    _link(who, row, suppliers.PAYMENT_PREFIX + entry_key, PAID if how == MANUAL else AUTO)


def to_inbox(who, row) -> str:
    """Строку — в инбокс: учётная строка «Не разобрано», статью даст разбор.

    С этого момента сумма в P&L (D076), и разбирается она там же, где
    остальные строки без статьи. Приход ложится минусом: возврат уменьшает
    расход, а не становится выручкой — выручку приносит коннектор.
    """
    _open(row)
    landing = cash.landing_for(who.tenant_id, row.op_date)
    key = INBOX_PREFIX + str(row.id)
    payload = {
        "tenant_id": str(who.tenant_id),
        "period": landing.period.isoformat(),
        "doc_date": row.op_date.isoformat(),
        "pnl_item_id": str(suppliers.line(suppliers.UNCLASSIFIED).id),
        "counterparty_id": _id(row.counterparty_id),
        "ledger": "official",
        "amount": str(row.amount if row.direction == OUT else -row.amount),
        "currency": row.currency or None,
        "title": row.party_name or row.purpose or _("Платёж по выписке"),
        "note": row.purpose or None,
        "channel": "bank",
        "source": BANK_SOURCE,
        "source_ref": row.bank_ref or None,
        "dedup_key": key,
        "allocation": "pending",
    }
    with transaction.atomic():
        fact_id, _action = cash.write_fact({k: v for k, v in payload.items() if v is not None})
        _link(who, row, key, INBOX)
    return str(fact_id)


def mark_not_ours(who, row, why: str) -> None:
    _open(row)
    why = (why or "").strip()
    if not why:
        raise BankRefused(_("Напишите, почему платёж не наш: через полгода иначе не вспомнить."))
    row.not_ours_at = timezone.now()
    row.not_ours_by = who.user_id
    row.not_ours_why = why[:500]
    row.save(update_fields=["not_ours_at", "not_ours_by", "not_ours_why"])


def back_to_work(who, row) -> None:
    if row.not_ours_at is None:
        raise BankRefused(_("Строка и так в работе."))
    row.not_ours_at = None
    row.not_ours_by = None
    row.not_ours_why = None
    row.save(update_fields=["not_ours_at", "not_ours_by", "not_ours_why"])


def unlink(who, row) -> int:
    """Отвязать сопоставление с внесённым платежом. Заведённое — не трогает.

    Отвязать можно связь, у которой своей учётной строки нет: платёж внесли
    руками, выписка его только подтвердила. Оплата счёта и строка инбокса,
    заведённые из выписки, — уже учёт: их правят и удаляют на своих экранах
    (счёт, инбокс), и тогда связь обрывается сама.
    """
    removable = BankLineLink.objects.filter(line=row, how__in=[AUTO, MANUAL]).exclude(
        fact_key__startswith=suppliers.PAYMENT_PREFIX + "bank-"
    )
    removed, _detail = removable.delete()
    if not removed:
        raise BankRefused(_(
            "Отвязать нечего: платёж заведён из выписки — исправьте его в счёте или инбоксе."
        ))
    return removed


def links_of(row) -> list[BankLineLink]:
    return list(BankLineLink.objects.filter(line=row).order_by("linked_at"))


def facts_by_key(keys) -> dict[str, Fact]:
    """Действующая учётная строка по ключу связи — для показа «чем закрыто»."""
    keys = list(keys)
    wanted = keys + [k + cash.FIX_SUFFIX for k in keys]
    found = {}
    for fact in (Fact.objects.filter(dedup_key__in=wanted, superseded_at__isnull=True)
                 .exclude(allocation="allocated").select_related("counterparty", "document",
                                                                 "pnl_item", "expense_item")):
        found[base_key(fact.dedup_key)] = fact
    return found


def turnover(rows) -> Decimal:
    """Оборот показанных строк. Дубль — та же операция банка, пришедшая второй
    раз, — не считается: иначе одна оплата дала бы двойной оборот."""
    return sum((row.amount for row in rows if row.duplicate_of_id is None), Decimal("0"))
