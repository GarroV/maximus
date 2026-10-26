"""Сверка суммы и НДС — вторая рука разбора первички (T206, модуль 15).

Экран отвечает на один вопрос: какие разнесённые строки ещё никто не сверил
с бумагой. Инбокс — первая рука (чья строка и за что), этот экран — вторая;
одна страница — одна функция (D081).
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from . import cash, permissions, two_hands
from .format import EMPTY, day, ledger_title, money
from .i18n import month_title
from .principal import get_current_principal


def _rows(who) -> list[dict]:
    lines = list(
        two_hands.awaiting_check()
        .select_related("counterparty", "unit", "expense_item")
        .order_by("-doc_date", "created_at")
    )
    hands = two_hands.hands_of([line.id for line in lines])
    me = str(who.user_id)
    may_confirm = permissions.has(who, permissions.SUPPLIERS_CONFIRM)
    rows = []
    for line in lines:
        allocator, _confirmer = hands.get(str(line.id), ("", ""))
        rows.append({
            "id": str(line.id),
            "date": day(line.doc_date),
            "period": month_title(line.period),
            "counterparty": line.counterparty.title if line.counterparty else EMPTY,
            "title": line.title,
            "article": (cash.item_title(line.expense_item.titles)
                        if line.expense_item else EMPTY),
            "unit": (line.unit.code if line.unit
                     else (_("разнесена по точкам") if line.allocation == "split"
                           else _("Вся сеть"))),
            "ledger": ledger_title(line.ledger),
            "amount": line.amount,
            "amount_raw": f"{line.amount}",
            "amount_text": money(line.amount),
            "vat": money(line.vat_amount) if line.vat_amount is not None else EMPTY,
            "vat_rate": (f"{line.vat_rate.normalize():f} %" if line.vat_rate is not None
                         else ""),
            "allocated_by": allocator or EMPTY,
            # Своя строка — без кнопки, а со словами почему: кнопка, которая
            # отвечает отказом, хуже объяснения рядом.
            "own": me in (two_hands.last_allocator(line), str(line.created_by)),
            "may_confirm": may_confirm,
            "url": reverse("inbox-check-confirm", args=[line.id]),
            "invoice_url": (reverse("invoice", args=[line.document_id])
                            if line.document_id else ""),
        })
    return rows


def _page(request, who, *, failed: str = "", status: int = 200):
    rows = _rows(who)
    total = sum((row["amount"] for row in rows), Decimal("0"))
    return render(request, "web/suppliers/check.html", {
        "rows": rows,
        "counted": len(rows),
        "total_raw": f"{total}",
        "total_text": money(total),
        "waits_for": two_hands.deciders(who.tenant_id, permissions.SUPPLIERS_CONFIRM),
        "may_confirm": permissions.has(who, permissions.SUPPLIERS_CONFIRM),
        "no_right": (permissions.explain(who, permissions.SUPPLIERS_CONFIRM)
                     if not permissions.has(who, permissions.SUPPLIERS_CONFIRM) else ""),
        "inbox_url": reverse("inbox"),
        "notice": _("Строка сверена.") if request.GET.get("done") else "",
        "failed": failed,
    }, status=status)


def _no_membership(request):
    return render(request, "web/directory/denied.html", {
        "message": _("Вас ещё не завели ни к одному партнёру, поэтому сверять нечего."),
    }, status=403)


@login_required
def check(request):
    """Очередь сверки: разнесённые строки, которые ещё никто не сверил."""
    who = get_current_principal(request)
    if who is None or who.tenant_id is None:
        return _no_membership(request)
    return _page(request, who)


@login_required
def check_confirm(request, fact_id):
    """Сверено: сумма и НДС совпадают с бумагой. Только POST — это запись."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    who = get_current_principal(request)
    if who is None or who.tenant_id is None:
        return _no_membership(request)

    fact = two_hands.awaiting_check().filter(pk=fact_id).first()
    if fact is None:
        # Чужая строка, выдуманный номер и уже сверенная отвечают одинаково (D023).
        raise Http404("строка не найдена")
    try:
        two_hands.confirm(who, fact)
    except two_hands.ReviewRefused as refused:
        return _page(request, who, failed=refused.message, status=refused.http_status)
    return redirect(reverse("inbox-check") + "?done=1")
