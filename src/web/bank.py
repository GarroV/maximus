"""Банковская выписка в данных: загрузка, сопоставление, решения по строке (T198).

Разбор файла, ключ строки и правило сопоставления — в пакете `bankstatement`
(без базы). Здесь — то, что требует базы: что уже загружено, какие платежи и
счета внесены, и запись решения.

## Что делает загрузка

1. Тот же файл второй раз — отказ с отсылкой к первой загрузке: не заводится
   ни выписка, ни строки (`bank_statements_same_file_once`).
2. Счёт выписки обязателен: из файла, а если файл его не несёт (таблица) —
   из формы. Ключ строки всегда включает счёт, иначе одинаковые операции
   двух счетов партнёра сливались бы в дубль.
3. Строка, чей ключ уже есть у партнёра, ложится **дублем** со ссылкой на
   исходную: в учёт не идёт, остаётся видимой (эталон, модуль 7). Ключ тот
   же, а сумма, дата или направление другие — это не дубль, а расхождение:
   файл не загружается, и человеку называется строка.
4. Новые строки сводятся с внесённым по `bankstatement.matching`: однозначная
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
import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, Value
from django.db.models.functions import Concat
from django.utils import timezone
from django.utils.translation import gettext as _

from bankstatement.keys import line_keys
from bankstatement.matching import DATE_WINDOW_DAYS, FACT, INVOICE, Candidate, choose
from bankstatement.model import OUT
from bankstatement.reader import read
from bankstatement.values import norm
from core.models import (
    BankLine,
    BankLineLink,
    BankStatement,
    Counterparty,
    Fact,
    SourceDocument,
    Tenant,
)

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
# Длины текстов строки при приёме. Назначение платежа в банках — до 140
# знаков (SEPA) и до нескольких сотен в выгрузках; дальше — не назначение, а
# мусор, который потом поедет в отчёты и выгрузки.
MAX_PARTY_NAME = 200
MAX_PURPOSE = 500
MAX_ACCOUNT = 64
MAX_BANK_REF = 100
# Код валюты ISO 4217: три латинские буквы.
CURRENCY_CODE = re.compile(r"[A-Z]{3}")


class BankRefused(cash.CashRefused):
    """Решение по строке не принято. Текст показывается человеку как есть."""


def home_currency(tenant_id) -> str:
    """Валюта учёта партнёра — в ней счёт может оплатиться сам."""
    return (Tenant.objects.filter(pk=tenant_id)
            .values_list("base_currency", flat=True).first() or "").upper()


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


def upload(who, content: bytes, file_name: str, *, account: str = "",
           currency: str = "") -> Loaded:
    """Загрузить файл выписки. Отказ разбора — `bankstatement.reader.Unreadable`.

    `account` и `currency` — из формы: берутся, только если файл их не несёт.
    """
    parsed = read(content, file_name)
    account = (parsed.account or account or "").strip()[:MAX_ACCOUNT]
    if not account:
        raise BankRefused(_(
            "В файле нет номера счёта. Укажите счёт, по которому эта выписка: "
            "без него операции двух счетов не отличить друг от друга."
        ))
    currency = _currency(parsed.currency, currency)
    content_hash = hashlib.sha256(content).hexdigest()
    earlier = _earlier(who, content_hash)
    if earlier is not None:
        raise AlreadyLoaded(earlier)
    try:
        return _store(who, parsed, account, currency, file_name, content_hash)
    except IntegrityError as error:
        # Тот же файл или те же строки загружают в этот же момент ещё раз:
        # проверка выше прошла у обоих, ограничение базы пустило одного.
        earlier = _earlier(who, content_hash)
        if earlier is not None:
            raise AlreadyLoaded(earlier) from error
        raise BankRefused(_(
            "Выписку по этому счёту сейчас загружают ещё раз — повторите через минуту."
        )) from error


def _currency(in_file: str, in_form: str) -> str:
    """Валюта выписки: из файла, а если файл её не несёт — выбранная явно.

    Валюту учёта партнёра молча не подставляем: выписка валютного счёта,
    загруженная «в динарах», свела бы евро с динаровыми платежами.
    """
    chosen = (in_file or in_form or "").strip().upper()
    if not chosen:
        raise BankRefused(_(
            "В файле нет валюты. Укажите валюту счёта — трёхбуквенный код, например RSD или EUR."
        ))
    if not CURRENCY_CODE.fullmatch(chosen):
        raise BankRefused(_(
            "Валюта — трёхбуквенный латинский код, например RSD или EUR, а не «%(value)s»."
        ) % {"value": chosen[:10]})
    return chosen


def _earlier(who, content_hash: str):
    return BankStatement.objects.filter(
        tenant_id=who.tenant_id, content_hash=content_hash,
    ).first()


def _store(who, parsed, account: str, currency: str, file_name: str,
           content_hash: str) -> Loaded:
    keys = line_keys(account, parsed.lines)
    with transaction.atomic():
        statement = BankStatement.objects.create(
            tenant_id=who.tenant_id, account=account, bank=parsed.bank,
            currency=currency, file_name=file_name[:200], format=parsed.format,
            content_hash=content_hash, period_from=parsed.period_from,
            period_to=parsed.period_to, uploaded_by=who.user_id,
            stats={"mapping": parsed.mapping},
        )
        known = {
            key: (pk, amount, direction, on)
            for key, pk, amount, direction, on in BankLine.objects.filter(
                tenant_id=who.tenant_id, dedup_key__in=keys, duplicate_of__isnull=True,
            ).values_list("dedup_key", "id", "amount", "direction", "op_date")
        }
        parties = PartyIndex(who.tenant_id)
        created, conflicts = [], []
        for number, (line, key) in enumerate(zip(parsed.lines, keys, strict=True), start=1):
            first = known.get(key)
            if first is not None and first[1:] != (line.amount, line.direction, line.op_date):
                conflicts.append(_conflict(number, line, first))
                continue
            row = BankLine.objects.create(
                tenant_id=who.tenant_id, statement=statement, line_no=number,
                op_date=line.op_date, direction=line.direction, amount=line.amount,
                currency=(line.currency or currency).upper()[:3],
                party_name=line.party_name[:MAX_PARTY_NAME],
                party_account=line.party_account[:MAX_ACCOUNT],
                purpose=line.purpose[:MAX_PURPOSE], bank_ref=line.bank_ref[:MAX_BANK_REF],
                dedup_key=key, duplicate_of_id=first[0] if first else None,
                counterparty_id=parties.find(line.party_name),
            )
            created.append(row)
            if first is None:
                known[key] = (row.id, row.amount, row.direction, row.op_date)
        if conflicts:
            # Исключение откатывает всю загрузку: половина файла хуже, чем ничего.
            raise BankRefused(_(
                "Файл не загружен: операции банка с теми же номерами уже загружены с "
                "другой суммой, датой или направлением. Проверьте, та ли это выписка "
                "и тот ли счёт. %(lines)s"
            ) % {"lines": " ".join(conflicts[:5])})
        fresh = [row for row in created if row.duplicate_of_id is None]
        auto_match(who, fresh)
        counts = count_states(statement_lines(statement))
        statement.stats = {"mapping": parsed.mapping, "counts": counts}
        statement.save(update_fields=["stats"])
    return Loaded(statement=statement, counts=counts)


def _conflict(number: int, line, first) -> str:
    _pk, amount, direction, on = first
    sign = {OUT: "−", "in": "+"}
    return _("Строка %(n)s: было %(was)s, в файле %(now)s.") % {
        "n": number,
        "was": f"{on:%d.%m.%Y} {sign.get(direction, '')}{amount:.2f}",
        "now": f"{line.op_date:%d.%m.%Y} {sign.get(line.direction, '')}{line.amount:.2f}",
    }


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


def _alive() -> Q:
    """Условие на связь: за ней стоит живой факт — исходный без сторно или
    исправление.

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

    return (Q(Exists(active(cash.FIX_SUFFIX)))
            | (Q(Exists(active())) & ~Q(Exists(active(cash.STORNO_SUFFIX)))))


def _alive_links():
    return BankLineLink.objects.filter(line_id=OuterRef("pk")).filter(_alive())


def with_state(rows):
    """Состояние строки и пометка «свело правило»: автоматическое решение
    видно в самом списке, а не только в карточке строки."""
    return rows.annotate(matched=Exists(_alive_links()),
                         by_rule=Exists(_alive_links().filter(how=AUTO)))


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


def line_for_decision(who, line_id):
    """Строка под решение — с блокировкой: два решения по одной строке
    одновременно не должны лечь оба. Блокируется только сама строка (`of`),
    состояние считается отдельным запросом: агрегат рядом с `for update`
    база не допускает."""
    row = (BankLine.objects.select_for_update(of=("self",))
           .select_related("counterparty", "statement")
           .filter(tenant_id=who.tenant_id, pk=line_id).first())
    if row is not None:
        row.matched = with_state(BankLine.objects.filter(pk=row.pk)).values_list(
            "matched", flat=True).first()
    return row


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
    """Платежи, уже сведённые с какой-то строкой. Мёртвая связь (её факт
    удалён) платёж не держит: иначе он не предлагался бы никому."""
    return set(BankLineLink.objects.filter(tenant_id=tenant_id).filter(_alive())
               .values_list("fact_key", flat=True))


def payment_candidates(row, *, days: int, home: str) -> list[tuple[Candidate, Fact]]:
    """Свободные внесённые платежи той же суммы и валюты в окне дат.

    Приход сводится с отрицательным платежом (возврат поставщика): знак у
    строки выписки в направлении, у факта — в сумме. Факт без валюты — в
    валюте учёта партнёра (`home`). Строка без валюты не сводится ни с чем.
    """
    if not row.currency:
        return []
    signed = row.amount if row.direction == OUT else -row.amount
    taken = _taken(row.tenant_id)
    found = _payment_facts().filter(
        tenant_id=row.tenant_id, amount=signed,
        doc_date__gte=row.op_date - timedelta(days=days),
        doc_date__lte=row.op_date + timedelta(days=days),
    ).order_by("doc_date", "created_at")
    candidates = []
    for fact in found:
        currency = (fact.currency or home).upper()
        if base_key(fact.dedup_key) in taken or currency != row.currency:
            continue
        candidates.append((Candidate(
            kind=FACT, ref=base_key(fact.dedup_key), amount=row.amount, on=fact.doc_date,
            party=_id(fact.counterparty_id), currency=currency,
        ), fact))
    return candidates


def open_invoices(who, home: str) -> list[tuple[Candidate, object]]:
    """Счета с остатком к оплате: остаток = сумма счёта минус платежи.
    Валюта счёта — его собственная, а без неё — валюта учёта партнёра."""
    listed = suppliers.invoices(who, {})
    paid = suppliers.paid_by_document([item.document.id for item in listed])
    found = []
    for item in listed:
        left = suppliers.outstanding(item.amount, paid.get(str(item.document.id)))
        if left > 0:
            found.append((Candidate(
                kind=INVOICE, ref=str(item.document.id), amount=left,
                on=item.document.doc_date, party=_id(item.document.counterparty_id),
                currency=(item.document.currency or home).upper(),
            ), item))
    return found


def _id(value) -> str | None:
    return str(value) if value is not None else None


# --- сопоставление ----------------------------------------------------------------


def auto_match(who, rows) -> int:
    """Свести то, что сводится однозначно. Возвращает, сколько свело.

    Счёт сам не оплачивается: оплата по счёту без человека допустима только
    при совпадении счёта получателя, а счетов контрагентов в справочнике пока
    нет. По одному названию из выписки счёт только предлагается в карточке.
    Сводятся сами лишь уже внесённые платежи.
    """
    if not rows:
        return 0
    home = home_currency(who.tenant_id)
    matched = 0
    for row in rows:
        facts = [c for c, _f in payment_candidates(row, days=DATE_WINDOW_DAYS, home=home)]
        chosen = choose(row.amount, row.op_date, _id(row.counterparty_id), facts, [],
                        currency=row.currency, party_by_account=False, home_currency=home)
        if chosen.kind != FACT:
            continue
        # Каждая попытка — своей точкой сохранения: отказ (политика не пустила)
        # оставляет строку ждать человека, а не роняет всю загрузку.
        try:
            with transaction.atomic():
                _link(who, row, chosen.ref, AUTO)
        except (cash.CashRefused, IntegrityError):
            continue
        matched += 1
    return matched


def _link(who, row, fact_key: str, how: str) -> None:
    # Мёртвая связь другой строки с тем же ключом уступает место живой:
    # ограничение «один факт — одна строка» иначе держало бы платёж за ней.
    BankLineLink.objects.filter(tenant_id=who.tenant_id, fact_key=fact_key).exclude(
        _alive()).delete()
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


def _drop_dead_links(row) -> None:
    """Связи строки, которая ждёт решения, — мёртвые: их факт удалён или
    сторнирован. Перед новым решением они убираются, иначе новая связь
    упёрлась бы в «одна связь на строку», а ключ — в «один факт на строку»."""
    BankLineLink.objects.filter(tenant_id=row.tenant_id, line=row).delete()


def _free_key(tenant_id, base: str) -> str:
    """Ключ записи, который ещё не занят ни одной версией факта.

    Строку решают заново, когда прежняя запись удалена; повторить её ключ
    значило бы попасть в «уже удалено» вместо новой записи.
    """
    taken = set(Fact.objects.filter(tenant_id=tenant_id, dedup_key__startswith=base)
                .values_list("dedup_key", flat=True))
    if not any(key == base or key.startswith(base + "#") for key in taken):
        return base
    number = 2
    while any(key == f"{base}-{number}" or key.startswith(f"{base}-{number}#")
              for key in taken):
        number += 1
    return f"{base}-{number}"


def link(who, row, fact_key: str) -> None:
    """Человек выбрал внесённый платёж: та же сумма, свободный, в окне показа."""
    _open(row)
    home = home_currency(who.tenant_id)
    offered = {c.ref for c, _f in payment_candidates(row, days=SHOWN_WINDOW_DAYS, home=home)}
    if fact_key not in offered:
        raise BankRefused(_(
            "Этот платёж не подходит: сумма другая, он уже сведён с другой строкой "
            "или вам не виден."
        ))
    try:
        with transaction.atomic():
            _drop_dead_links(row)
            _link(who, row, fact_key, MANUAL)
    except IntegrityError as error:
        # Тот же платёж в этот же момент свели с другой строкой.
        raise BankRefused(_(
            "Этот платёж только что сведён с другой строкой. Обновите страницу и "
            "выберите другой."
        )) from error


def pay(who, row, document_id: str) -> None:
    """Строка выписки — оплата этого счёта: заводится платёж с датой банка."""
    _open(row)
    if row.direction != OUT:
        raise BankRefused(_("Поступление не оплачивает счёт поставщика."))
    with transaction.atomic():
        _drop_dead_links(row)
        _pay(who, row, document_id)


def _pay(who, row, document_id: str) -> None:
    document = suppliers.document_or_none(document_id)
    # Счёт — того же партнёра, что строка и человек: явным условием, не только
    # политикой (см. `line_or_none`).
    if document is None or document.tenant_id != who.tenant_id:
        raise BankRefused(_("Счёт не найден."))
    # Счёт блокируется до расчёта остатка: два платежа по нему одновременно
    # иначе оба увидели бы остаток целиком.
    SourceDocument.objects.select_for_update().filter(pk=document.pk).first()
    currency = (document.currency or home_currency(who.tenant_id)).upper()
    if not row.currency or currency != row.currency:
        raise BankRefused(_(
            "Валюта счёта (%(invoice)s) и платежа (%(line)s) разная — такой платёж этот "
            "счёт не оплачивает. Отправьте строку в инбокс или выберите счёт в той же валюте."
        ) % {"invoice": currency or "—", "line": row.currency or "—"})
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
    entry_key = _free_key(
        who.tenant_id, suppliers.PAYMENT_PREFIX + f"bank-{row.id}",
    ).removeprefix(suppliers.PAYMENT_PREFIX)
    suppliers.pay(
        who, document, entry_key=entry_key, on=row.op_date, amount=row.amount,
        till_id=None, ledger=primary.ledger if primary else "official",
        note=row.purpose[:500],
    )
    _link(who, row, suppliers.PAYMENT_PREFIX + entry_key, PAID)


def to_inbox(who, row) -> str:
    """Строку — в инбокс: учётная строка «Не разобрано», статью даст разбор.

    С этого момента сумма в P&L (D076), и разбирается она там же, где
    остальные строки без статьи. Приход ложится минусом: возврат уменьшает
    расход, а не становится выручкой — выручку приносит коннектор.
    """
    _open(row)
    landing = cash.landing_for(who.tenant_id, row.op_date)
    key = _free_key(who.tenant_id, INBOX_PREFIX + str(row.id))
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
        _drop_dead_links(row)
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


def facts_by_key(tenant_id, keys) -> dict[str, Fact]:
    """Действующая учётная строка по ключу связи — для показа «чем закрыто»."""
    keys = list(keys)
    wanted = keys + [k + cash.FIX_SUFFIX for k in keys]
    found = {}
    for fact in (Fact.objects.filter(tenant_id=tenant_id, dedup_key__in=wanted,
                                     superseded_at__isnull=True)
                 .exclude(allocation="allocated").select_related("counterparty", "document",
                                                                 "pnl_item", "expense_item")):
        found[base_key(fact.dedup_key)] = fact
    return found


def turnover(rows) -> Decimal:
    """Оборот показанных строк. Дубль — та же операция банка, пришедшая второй
    раз, — не считается: иначе одна оплата дала бы двойной оборот."""
    return sum((row.amount for row in rows if row.duplicate_of_id is None), Decimal("0"))
