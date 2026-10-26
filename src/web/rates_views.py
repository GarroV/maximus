"""Курсы валют для консолидации сети: ввод и список (T209).

**Кто вводит — администратор платформы.** Справочник курсов общий для всех
партнёров (`fx_rates` без тенанта): партнёр, правящий его, двигал бы цифру
соседу. Право спрашивает и база (политика `platform_admin_enters`, `0277`), и
эта страница — чтобы не предлагать форму тому, кому база откажет.

**Курс вводится так, как его публикуют**: «1 EUR = 117,1834 RSD». Откуда он
взят — словами в поле источника: источник курсов не выбран (ручной ввод или
лента), и через полгода без этой строки никто не скажет, чем считали июнь.

**Исправить курс — ввести новый с той же датой.** Строка справочника не
правится и не удаляется: закрытые месяцы ссылаются на неё, и правка на месте
сдвинула бы их. Новая строка действует на открытые месяцы с момента ввода.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, transaction
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from core.models import FxRate
from core.spaces import is_platform_admin

from .format import day, exact
from .platform_views import _refuse
from .principal import get_current_principal

CURRENCY = re.compile(r"^[A-Z]{3}$")
# Столько показываем: справочник растёт на строку в месяц на пару, и полгода
# истории хватает, чтобы увидеть, чем считались незакрытые и недавние месяцы.
SHOWN = 100
# Пределы колонки `fx_rates.rate` (18 знаков, 8 после запятой): больше — отказ
# словами здесь, а не ошибка базы.
RATE_PLACES = 8
# Курс публикуется на прошедший или текущий день; дальше недели вперёд — почти
# наверняка опечатка в годе, а такой курс тихо станет курсом всех будущих месяцев.
AHEAD_DAYS = 7
NOTE_LIMIT = 200
RATE_WHOLE_DIGITS = 10


class BadRate(ValueError):
    """Курс не принят — с причиной, которую видит человек."""


@login_required
def rates(request):
    """Список курсов и форма нового — одна страница: вводят, глядя на прежние."""
    who = get_current_principal(request)
    user_id = who.user_id if who else None
    if not is_platform_admin(user_id):
        return _refuse(request)
    if request.method != "POST":
        return _page(request, notice=request.session.pop("rates_notice", ""))

    try:
        entered = _parse(request.POST)
        with transaction.atomic():
            FxRate.objects.create(entered_by=user_id, **entered)
    except BadRate as refusal:
        return _page(request, error=str(refusal), filled=request.POST, status=400)
    except DatabaseError:
        return _page(request, error=_("База не приняла курс. Проверьте поля и повторите."),
                     filled=request.POST, status=400)

    request.session["rates_notice"] = _("Курс на %(on)s записан.") % {
        "on": day(entered["rate_date"])
    }
    return redirect("platform-rates")


def _parse(data) -> dict:
    base = (data.get("base_currency") or "").strip().upper()
    quote = (data.get("quote_currency") or "").strip().upper()
    if not CURRENCY.fullmatch(base) or not CURRENCY.fullmatch(quote):
        raise BadRate(_("Валюта — три латинские буквы кода ISO: EUR, RSD, HUF."))
    if base == quote:
        raise BadRate(_("Курс связывает две разные валюты."))
    raw_day = (data.get("rate_date") or "").strip()
    try:
        on = datetime.strptime(raw_day, "%Y-%m-%d").date()
    except ValueError:
        raise BadRate(_("Дата курса пишется как 2026-06-30.")) from None
    if on > date.today() + timedelta(days=AHEAD_DAYS):
        raise BadRate(_("Курс на дату дальше недели вперёд не вводится — проверьте год."))
    note = (data.get("note") or "").strip()
    if len(note) > NOTE_LIMIT:
        raise BadRate(_("Источник — не длиннее %(limit)s знаков.") % {"limit": NOTE_LIMIT})
    raw_rate = (data.get("rate") or "").strip().replace(" ", "").replace(",", ".")
    try:
        value = Decimal(raw_rate)
    except InvalidOperation:
        raise BadRate(_("Курс — число: 117,1834.")) from None
    if not value.is_finite() or value <= 0:
        raise BadRate(_("Курс — положительное число."))
    sign, digits, exponent = value.as_tuple()
    if -exponent > RATE_PLACES or len(digits) + exponent > RATE_WHOLE_DIGITS:
        raise BadRate(_("Курс — не больше восьми знаков после запятой и десяти до неё."))
    return {
        "base_currency": base, "quote_currency": quote, "rate_date": on,
        "rate": value, "note": note,
    }


def _page(request, *, notice: str = "", error: str = "", filled=None, status: int = 200):
    filled = filled or {}
    rows = FxRate.objects.order_by("-rate_date", "-entered_at")[:SHOWN]
    return render(request, "web/platform/rates.html", {
        "notice": notice,
        "error": error,
        "filled": {
            "rate_date": filled.get("rate_date") or _last_month_end().isoformat(),
            "base_currency": filled.get("base_currency") or "EUR",
            "quote_currency": filled.get("quote_currency") or "",
            "rate": filled.get("rate") or "",
            "note": filled.get("note") or "",
        },
        "rates": [
            {
                "on": day(row.rate_date),
                "text": f"1 {row.base_currency} = {exact(row.rate)} {row.quote_currency}",
                "note": row.note,
                "entered": day(row.entered_at.date()),
            }
            for row in rows
        ],
    }, status=status)


def _last_month_end() -> date:
    """Курс вводят чаще всего на конец прошлого месяца — его и подставляем."""
    today = date.today()
    return date.fromordinal(today.replace(day=1).toordinal() - 1)
