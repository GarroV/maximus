"""Экраны банковской выписки: сопоставление и загрузка (T198, модуль 7 эталона).

Две страницы, а не одна с вкладками (D081: одна страница — одна функция):

* **Сопоставление** (`/statement/`) отвечает на вопрос «что из выписки ещё не
  сведено и чем закрыть строку». Наверху число ждущих решения, под ним отбор
  месяцем и состоянием, под ним строки, справа карточка выбранной строки с
  решениями.
* **Загрузка** (`/statement/upload/`) — «загрузилось ли и как банк понят»:
  форма файла, как разобраны колонки, что легло само, что дублем.

Правила о деньгах здесь не живут — они в `web/bank.py` и `bankstatement`.
Доступ — правом разбора первички: страница его спрашивает, чтобы ответить
словами, а базу закрывают политики `0276`.
"""
from __future__ import annotations

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from bankstatement.model import OUT
from bankstatement.reader import MAX_BYTES, Unreadable
from core.models import BankLine, BankStatement

from . import bank, cash, filter_links, permissions, suppliers
from .format import EMPTY, day, money
from .principal import get_current_principal

MAX_SHOWN_UPLOADS = 12

# Отбор по состоянию: «дубли и чужое» — одной группой, как в эталоне: обе в
# учёт не идут и смотрят их вместе.
ASIDE = "aside"


def _state_titles() -> dict:
    return {
        bank.MATCHED: _("Сопоставлено"),
        bank.DECIDE: _("Нужно решить"),
        bank.DUPLICATE: _("Дубль"),
        bank.NOT_OURS: _("Не наш"),
    }


def _how_titles() -> dict:
    return {
        bank.AUTO: _("свело правило"),
        bank.MANUAL: _("выбрано вручную"),
        bank.PAID: _("оплата счёта из выписки"),
        bank.INBOX: _("отправлено в инбокс"),
    }


def _who(request):
    """Вошедший с партнёром и правом разбора — или готовый отказ словами."""
    who = get_current_principal(request)
    if who is None or who.tenant_id is None:
        return None, render(request, "web/directory/denied.html", {"message": _(
            "Вас ещё не завели ни к одному партнёру, поэтому выписки у вас нет."
        )}, status=403)
    if not permissions.has(who, permissions.SUPPLIERS_CLASSIFY):
        return None, render(request, "web/directory/denied.html", {
            "message": permissions.explain(who, permissions.SUPPLIERS_CLASSIFY),
        }, status=403)
    return who, None


def _tabs(current: str) -> list[dict]:
    return [
        {"title": _("Сопоставление"), "url": reverse("statement"),
         "selected": current == "match"},
        {"title": _("Загрузка"), "url": reverse("statement-upload"),
         "selected": current == "upload"},
    ]


# --- сопоставление ---------------------------------------------------------------


def _period(params, who) -> dict:
    """Месяц экрана. Умолчание — месяц последней загруженной операции.

    Не текущий месяц, как у кассы: выписку за июнь грузят в июле, и экран,
    открывшийся на пустом июле сразу после загрузки, читался бы как «ничего
    не загрузилось».
    """
    try:
        start = date.fromisoformat(params.get("from", ""))
        end = date.fromisoformat(params.get("to", ""))
        if start <= end:
            return {"from": start, "to": end}
    except ValueError:
        pass
    latest = (BankLine.objects.filter(tenant_id=who.tenant_id)
              .order_by("-op_date").values_list("op_date", flat=True).first())
    first, last = filter_links.month_bounds(latest or date.today())
    return {"from": first, "to": last}


@login_required
def statement(request):
    who, refused = _who(request)
    if refused:
        return refused
    return _statement_page(request, who, request.GET)


def _statement_page(request, who, params, *, error: str = "", status: int = 200):
    """Страница сопоставления. Отказ решения рисуется ею же, а не адресом.

    Текст отказа не ездит в адресе: сообщение, взятое из адреса, подделывается
    ссылкой — страница показала бы продуктовым голосом что угодно.
    """
    chosen = _period(params, who)
    default = {"from": None, "to": None, "state": ""}
    shown_state = params.get("state", "")
    if shown_state not in ("", bank.DECIDE, bank.MATCHED, ASIDE):
        shown_state = ""
    query = filter_links.query_of({**chosen, "state": shown_state}, default)
    base = reverse("statement")

    rows = list(bank.with_state(
        BankLine.objects.filter(tenant_id=who.tenant_id, op_date__gte=chosen["from"],
                                op_date__lte=chosen["to"])
        .select_related("counterparty")
    ).order_by("op_date", "statement__created_at", "line_no"))
    states = {row.id: bank.state_of(row) for row in rows}
    counts = bank.count_states(rows)

    def kept(row) -> bool:
        state = states[row.id]
        if shown_state == ASIDE:
            return state in (bank.DUPLICATE, bank.NOT_OURS)
        return not shown_state or state == shown_state

    shown = [row for row in rows if kept(row)]
    waiting = [row for row in rows if states[row.id] == bank.DECIDE]
    titles = _state_titles()
    filters = [
        filter_links.months(chosen, base=base, query=query),
        filter_links.row("state", _("Состояние"), [
            (bank.DECIDE, f"{titles[bank.DECIDE]} · {counts[bank.DECIDE]}"),
            (bank.MATCHED, f"{titles[bank.MATCHED]} · {counts[bank.MATCHED]}"),
            (ASIDE, _("Дубли и чужое") + f" · {counts[bank.DUPLICATE] + counts[bank.NOT_OURS]}"),
        ], shown_state, base=base, query=query, everything=_("Все") + f" · {len(rows)}"),
    ]
    selected_id = params.get("line", "")
    selected = bank.line_or_none(who, selected_id) if _is_uuid(selected_id) else None
    if selected is None and waiting and not selected_id:
        selected = bank.line_or_none(who, waiting[0].id)
    if selected_id and selected is None:
        raise Http404("строка не найдена")

    return render(request, "web/bank/statement.html", {
        "tabs": _tabs("match"),
        "filters": filters,
        "waiting": len(waiting),
        "waiting_sum_raw": f"{bank.turnover(waiting)}",
        "waiting_sum": money(bank.turnover(waiting)),
        "counted": len(rows),
        "shown_count": len(shown),
        "shown_turnover": money(bank.turnover(shown)),
        "rows": [_row(row, states[row.id], selected, base, query) for row in shown],
        "card": _card(who, selected) if selected is not None else None,
        "notice": _notice(params),
        "error": error,
        "upload_url": reverse("statement-upload"),
        "has_any": BankStatement.objects.filter(tenant_id=who.tenant_id).exists(),
    }, status=status)


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def _signed(row) -> str:
    return ("+" if row.direction != OUT else "−") + money(row.amount)


def _row(row, state: str, selected, base: str, query: dict) -> dict:
    return {
        "id": row.id,
        "date": day(row.op_date),
        "who": row.counterparty.title if row.counterparty_id else (row.party_name or EMPTY),
        "purpose": row.purpose or EMPTY,
        "sum": _signed(row),
        "incoming": row.direction != OUT,
        "state": state,
        "state_title": (_("Сопоставлено правилом")
                        if state == bank.MATCHED and getattr(row, "by_rule", False)
                        else _state_titles()[state]),
        "by_rule": state == bank.MATCHED and getattr(row, "by_rule", False),
        "selected": selected is not None and selected.id == row.id,
        "url": filter_links.url(base, query, line=str(row.id)),
    }


def _card(who, row) -> dict:
    state = bank.state_of(row)
    card = {
        "id": row.id,
        "state": state,
        "state_title": _state_titles()[state],
        "date": day(row.op_date),
        "sum": _signed(row),
        "sum_raw": f"{row.amount}",
        "party_name": row.party_name or EMPTY,
        "party_account": row.party_account,
        "purpose": row.purpose or EMPTY,
        "bank_ref": row.bank_ref,
        "file": row.statement.file_name,
        "counterparty": row.counterparty.title if row.counterparty_id else "",
        "act_url": reverse("statement-line", args=[row.id]),
        "is_out": row.direction == OUT,
    }
    if state == bank.DUPLICATE:
        original = row.duplicate_of
        card["original"] = {
            "file": original.statement.file_name,
            "loaded": day(original.statement.created_at.date()),
            "line_no": original.line_no,
            "url": reverse("statement") + f"?line={original.id}",
        }
    elif state == bank.NOT_OURS:
        card["not_ours_why"] = row.not_ours_why
        card["not_ours_on"] = day(row.not_ours_at.date())
    elif state == bank.MATCHED:
        card["closed_by"] = _closed_by(row)
    else:
        card["payments"] = _payments(row)
        card["invoices"] = _invoices(who, row) if row.direction == OUT else []
    return card


def _closed_by(row) -> list[dict]:
    links = bank.links_of(row)
    facts = bank.facts_by_key(row.tenant_id, (link.fact_key for link in links))
    hows = _how_titles()
    closed = []
    for link in links:
        fact = facts.get(link.fact_key)
        if fact is None:
            continue
        closed.append({
            "title": fact.title,
            "date": day(fact.doc_date),
            "sum": money(fact.amount),
            "how": hows.get(link.how, link.how),
            "url": _fact_url(fact),
            "may_unlink": link.how in (bank.AUTO, bank.MANUAL)
            and not link.fact_key.startswith(suppliers.PAYMENT_PREFIX + "bank-"),
        })
    return closed


def _fact_url(fact) -> str:
    if fact.dedup_key.startswith(bank.INBOX_PREFIX):
        return reverse("inbox")
    if fact.document_id:
        return reverse("invoice", args=[fact.document_id])
    return reverse("invoices")


def _payments(row) -> list[dict]:
    return [
        {"key": candidate.ref, "title": fact.title,
         "who": fact.counterparty.title if fact.counterparty_id else EMPTY,
         "date": day(fact.doc_date), "sum": money(fact.amount)}
        for candidate, fact in bank.payment_candidates(
            row, days=bank.SHOWN_WINDOW_DAYS, home=bank.home_currency(row.tenant_id))
    ]


def _invoices(who, row) -> list[dict]:
    """Счета к оплате: опознанного получателя — все с остатком не меньше суммы;
    неопознанного — только с остатком ровно на сумму строки."""
    shown = []
    for candidate, item in bank.open_invoices(who, bank.home_currency(who.tenant_id)):
        if candidate.currency != row.currency:
            continue  # счёт в другой валюте этой строкой не оплачивается
        if row.counterparty_id is not None:
            fits = candidate.party == str(row.counterparty_id) and candidate.amount >= row.amount
        else:
            fits = candidate.amount == row.amount
        if not fits:
            continue
        document = item.document
        shown.append({
            "id": document.id,
            "title": document.doc_number or _("без номера"),
            "who": document.counterparty.title if document.counterparty_id else EMPTY,
            "date": day(document.doc_date),
            "left": money(candidate.amount),
            "exact": candidate.amount == row.amount,
        })
    return shown


def _notice(params) -> str:
    return {
        "linked": _("Строка сопоставлена с внесённым платежом."),
        "paid": _("Оплата счёта заведена с датой из выписки."),
        "inbox": _("Строка ушла в инбокс: сумма в P&L строкой «Не разобрано», статью дайте там."),
        "not_ours": _("Строка помечена как не наш платёж и в учёт не идёт."),
        "back": _("Строка снова ждёт решения."),
        "unlinked": _("Сопоставление снято, строка снова ждёт решения."),
    }.get(params.get("done", ""), "")


@login_required
def statement_line(request, line_id):
    """Решение по строке. Только POST; ответ — назад к той же строке."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    who, refused = _who(request)
    if refused:
        return refused
    row = bank.line_for_decision(who, line_id)
    if row is None:
        raise Http404("строка не найдена")
    action = request.POST.get("action", "")
    try:
        if action == "link":
            bank.link(who, row, request.POST.get("fact", ""))
            done = "linked"
        elif action == "pay":
            bank.pay(who, row, request.POST.get("invoice", ""))
            done = "paid"
        elif action == "inbox":
            bank.to_inbox(who, row)
            done = "inbox"
        elif action == "not_ours":
            bank.mark_not_ours(who, row, request.POST.get("why", ""))
            done = "not_ours"
        elif action == "back":
            bank.back_to_work(who, row)
            done = "back"
        elif action == "unlink":
            bank.unlink(who, row)
            done = "unlinked"
        else:
            raise bank.BankRefused(_("Непонятное действие."))
    except cash.CashRefused as refusal:
        return _statement_page(request, who, _around(row), error=refusal.message, status=400)
    return redirect(filter_links.url(reverse("statement"), _around(row), done=done))


def _around(row) -> dict:
    """Месяц и строка, к которым вернуться после решения."""
    first, last = filter_links.month_bounds(row.op_date)
    return {"from": first.isoformat(), "to": last.isoformat(), "line": str(row.id)}


# --- загрузка -----------------------------------------------------------------------


@login_required
def statement_upload(request):
    who, refused = _who(request)
    if refused:
        return refused
    error, status = "", 200
    form = {"account": (request.POST.get("account") or "").strip()[:bank.MAX_ACCOUNT],
            "currency": (request.POST.get("currency") or "").strip().upper()[:8]}
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            error, status = _("Выберите файл выписки."), 400
        elif upload.size > MAX_BYTES:
            # Размер — до чтения: файл больше потолка в память не поднимается.
            error, status = _("Файл больше 5 МБ — это не выписка за месяц."), 400
        else:
            try:
                loaded = bank.upload(who, upload.read(), upload.name or "",
                                     account=form["account"], currency=form["currency"])
                return redirect(reverse("statement-upload") + f"?loaded={loaded.statement.id}")
            except Unreadable as bad:
                error, status = _("Файл не разобран: %(why)s") % {"why": str(bad)}, 400
            except bank.AlreadyLoaded as again:
                error, status = again.message, 409
            except cash.CashRefused as refusal:
                error, status = refusal.message, 400

    statements = list(BankStatement.objects.filter(tenant_id=who.tenant_id)
                      .order_by("-created_at")[:MAX_SHOWN_UPLOADS])
    loaded_id = request.GET.get("loaded", "")
    loaded = next((s for s in statements if str(s.id) == loaded_id), None)
    return render(request, "web/bank/upload.html", {
        "tabs": _tabs("upload"),
        "error": error,
        "form": form,
        "loaded": _loaded(loaded) if loaded else None,
        "statements": [_listed(s) for s in statements],
        "match_url": reverse("statement"),
    }, status=status)


def _format_title(code: str) -> str:
    return {
        "camt053": "ISO 20022 camt.053",
        "ibank": _("izvod iBank (Banca Intesa)"),
        "raiffeisen_rs": _("izvod Raiffeisen"),
        "table": _("таблица по заголовкам"),
    }.get(code, code)


def _field_titles() -> dict:
    return {
        "date": _("Дата"), "debit": _("Сумма, расход"), "credit": _("Сумма, приход"),
        "amount": _("Сумма со знаком"), "party_name": _("Контрагент"),
        "party_account": _("Счёт контрагента"), "purpose": _("Назначение"),
        "bank_ref": _("Идентификатор операции"),
    }


def _period_text(statement) -> str:
    if statement.period_from and statement.period_to:
        return f"{day(statement.period_from)} — {day(statement.period_to)}"
    return EMPTY


def _listed(statement) -> dict:
    counts = (statement.stats or {}).get("counts", {})
    return {
        "id": statement.id,
        "file": statement.file_name or EMPTY,
        "loaded": day(statement.created_at.date()),
        "period": _period_text(statement),
        "account": statement.account or EMPTY,
        "format": _format_title(statement.format),
        "lines": sum(counts.values()) if counts else EMPTY,
        "duplicates": counts.get(bank.DUPLICATE, 0),
        "url": reverse("statement-upload") + f"?loaded={statement.id}",
    }


def _loaded(statement) -> dict:
    """Итог загрузки — на момент загрузки (`stats`), а не пересчитанный сейчас:
    вопрос экрана «как лёг этот файл», а сегодняшнее состояние — на сопоставлении."""
    stats = statement.stats or {}
    counts = stats.get("counts", {})
    fields = _field_titles()
    return {
        **_listed(statement),
        "bank": statement.bank,
        "currency": statement.currency,
        "mapping": [{"from": source, "to": fields.get(target, target)}
                    for source, target in stats.get("mapping", [])],
        "matched": counts.get(bank.MATCHED, 0),
        "decide": counts.get(bank.DECIDE, 0),
        "duplicate": counts.get(bank.DUPLICATE, 0),
        "match_url": _match_url(statement),
    }


def _match_url(statement) -> str:
    start = statement.period_from or date.today()
    first, _last = filter_links.month_bounds(start)
    end = statement.period_to or _last
    return filter_links.url(reverse("statement"), {
        "from": first.isoformat(), "to": max(end, _last).isoformat(),
    })
