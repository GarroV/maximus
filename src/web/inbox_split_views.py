"""«Разнести по точкам» из инбокса пачкой (T234, эталон модуля 3; D089).

Одиночно строка на всю сеть разносится своим экраном «Чья накладная»
(`expenses_views.split_form`) — туда ведёт ссылка у строки. Здесь — пачка:
отмеченные строки делятся **одними** долями.

**Пачка только из строк одного юрлица.** Точки, на которые делится накладная, —
это точки её юрлица (`expenses_views._units_for`, та же проверка в базе
`split_fact_by_hand`). У строк разных юрлиц общих точек нет, и одни доли на
них не лягут: такая пачка получает отказ словами, а не половину разнесения.

**Доли считает и проверяет та же функция, что у одиночного экрана**
(`expenses_views._shares`): «поровну», «по выручке» (выручка — за период каждой
строки) или руками, ровно 100%. Разносит та же функция базы. Второй способ
делить одну сумму разошёлся бы с первым на первой неровной накладной.

**Либо вся пачка, либо ничего.** Своя транзакция: отказ на третьей строке
откатывает и первые две, иначе в инбоксе осталась бы пачка, разнесённая
наполовину, и какие строки разнесены — ищите сами.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from . import cash, suppliers
from .dbrefusal import BadInput
from .expenses_views import SHARE_PREFIX, _shares, _units_for
from .format import EMPTY, day, money
from .i18n import month_title
from .principal import get_current_principal

TEMPLATE = "web/suppliers/inbox_split.html"


class _Refused(Exception):
    """Отказ пачки словами и кодом — показывается на странице разнесения."""

    def __init__(self, message: str, status: int):
        self.message = message
        self.status = status
        super().__init__(message)


@login_required
def inbox_split(request):
    """Страница долей для отмеченных строк и само разнесение. Только POST.

    Первый POST приходит из инбокса с отмеченными строками и показывает доли;
    второй, с `apply`, разносит. GET не нужен: без отмеченных строк страница
    ни о чём, а строки в адресе — это номера фактов в истории браузера.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    who = get_current_principal(request)
    if who is None or who.tenant_id is None:
        return render(request, "web/directory/denied.html", {"message": _(
            "Вас ещё не завели ни к одному партнёру, поэтому разбирать нечего."
        )}, status=403)

    chosen = request.POST.getlist("facts")
    facts: list = []
    try:
        facts = _eligible(chosen)
        if request.POST.get("apply"):
            spread = _apply(request, who, facts)
            return redirect(reverse("inbox") + f"?spread={spread}")
    except _Refused as refused:
        return _page(request, who, facts, error=refused.message, status=refused.status)
    except BadInput as bad:
        return _page(request, who, facts, error=bad.message, status=bad.http_status)
    return _page(request, who, facts)


def _eligible(chosen: list[str]) -> list:
    """Отмеченные строки, если их можно разнести одной пачкой. Иначе отказ словами."""
    if not chosen:
        raise _Refused(_(
            "Отметьте строки, которые разносятся по точкам: без отметки разносить нечего."
        ), 400)

    facts = [suppliers.unclassified_fact(fact_id) for fact_id in chosen]
    if any(fact is None for fact in facts):
        # Чужая строка, выдуманный номер и уже разобранная отвечают одинаково (D023).
        raise Http404("строка не найдена")

    placed = [fact for fact in facts if fact.allocation != "pending" or fact.unit_id]
    if placed:
        raise _Refused(_(
            "Среди отмеченных есть строки, у которых точка уже есть или которые уже "
            "разнесены: %(what)s. Снимите с них отметку — разносить их нечего."
        ) % {"what": ", ".join(fact.title for fact in placed)}, 400)

    if len({fact.legal_entity_id for fact in facts}) > 1:
        raise _Refused(_(
            "Отмеченные строки относятся к разным юрлицам, а делятся накладные только "
            "на точки своего юрлица: общих точек у них нет. Разнесите их по одной или "
            "отметьте строки одного юрлица."
        ), 400)
    return facts


def _apply(request, who, facts) -> int:
    """Разнести всю пачку одними долями. Сколько строк разнесено."""
    with transaction.atomic():
        for fact in facts:
            written = cash.split_by_hand(fact.id, _shares(request, who, fact),
                                         getattr(who, "user_id", None))
            if written is None:
                raise _Refused(_(
                    "Разнести эту сумму может только тот, кто ведёт все точки "
                    "партнёра: строки разнесения ложатся и на чужие точки."
                ), 403)
            if not written:
                # Ноль строк — не успех: строку за это время разнесли или разобрали,
                # и считать её разнесённой значило бы сказать неправду.
                raise _Refused(_(
                    "Строка «%(what)s» не разнесена: её уже разнесли или разобрали. "
                    "Ничего из пачки не записано — откройте инбокс заново."
                ) % {"what": fact.title}, 409)
    return len(facts)


def _page(request, who, facts, *, error: str = "", status: int = 200):
    total = sum((fact.amount for fact in facts), Decimal("0"))
    return render(request, TEMPLATE, {
        "error": error,
        "rows": [
            {
                "id": str(fact.id),
                "date": day(fact.doc_date),
                "period": month_title(fact.period),
                "counterparty": fact.counterparty.title if fact.counterparty else EMPTY,
                "title": fact.title,
                "amount_raw": f"{fact.amount}",
                "amount_text": money(fact.amount),
            }
            for fact in facts
        ],
        "total_raw": f"{total}",
        "total_text": money(total),
        # Набранные доли возвращаются в поля на отказе: набирать их заново из-за
        # одной опечатки — верный способ разнести «поровну», лишь бы отстали.
        "units": ([{"id": str(unit.id), "code": unit.code,
                    "value": request.POST.get(f"{SHARE_PREFIX}{unit.id}", "")}
                   for unit in _units_for(who, facts[0])] if facts else []),
        "inbox_url": reverse("inbox"),
    }, status=status)


__all__ = ["inbox_split"]
