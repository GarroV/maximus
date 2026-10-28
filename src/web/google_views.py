"""Вход через Google и привязка аккаунта: уход к Google и возврат (T241, D098).

Один адрес возврата на оба случая — `/auth/google/callback`: он вписан в
приложение Google владельцем и меняться не должен. Чем был заход — входом или
привязкой со страницы учётной записи, — помнит сессия вместе с меткой `state`.

**Метка `state` и `nonce`.** Обе случайные, живут в сессии одного захода и
снимаются на возврате в любом исходе. `state` сверяется с вернувшимся из
Google — без этого чужая страница могла бы привести человека на наш возврат со
своим кодом и войти его браузером (CSRF входа). `nonce` сверяется с тем, что
Google положил внутрь `id_token`, — так токен привязан к этому заходу, а не
только код. Сессия Django живёт в cookie с `SameSite=Lax` и путём `/`: при
возврате из Google это переход верхнего уровня, и cookie до нас доходит —
в том числе через фронт Cloudflare, который адрес и query не меняет.

**Куда вернуться после входа** — только внутрь продукта (`views.safe_next`),
проверяется на уходе и хранится в сессии: из адреса возврата его не берём.
"""
from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import google_auth, google_login
from .principal import get_current_principal

log = logging.getLogger(__name__)

FLOW_KEY = "google_flow"
LOGIN, LINK = "login", "link"


def _settings() -> google_auth.GoogleSettings:
    configured = getattr(settings, "GOOGLE_AUTH", None)
    if configured is None:
        # Нет реквизитов — нет и входа: кнопки на странице нет, а прямой заход
        # получает 404, а не полпути до Google.
        raise Http404("google sign-in is not configured")
    return configured


def _go_to_google(request, purpose: str, next_url: str = ""):
    config = _settings()
    state, nonce = google_auth.new_token(), google_auth.new_token()
    request.session[FLOW_KEY] = {
        "state": state, "nonce": nonce, "purpose": purpose, "next": next_url,
    }
    return redirect(google_auth.authorization_url(config, state=state, nonce=nonce))


def start(request):
    """«Войти через Google» со страницы входа."""
    from .views import safe_next

    return _go_to_google(request, LOGIN, safe_next(request))


@require_POST
def link_start(request):
    """«Привязать Google» со страницы учётной записи — только вошедшему."""
    if get_current_principal(request) is None:
        return redirect(reverse("login"))
    return _go_to_google(request, LINK)


@require_POST
def unlink(request):
    """Отвязать аккаунт Google от своей учётки."""
    who = get_current_principal(request)
    if who is None:
        return redirect(reverse("login"))
    google_login.unlink_google(who.user_id)
    return redirect(reverse("account") + "?google=unlinked")


def _refuse(purpose: str, code: str):
    if purpose == LINK:
        return redirect(reverse("account") + f"?google={code}")
    return redirect(reverse("login") + f"?google={code}")


def callback(request):
    """Возврат из Google: сверка метки, обмен кода, вход или привязка."""
    config = _settings()
    flow = request.session.pop(FLOW_KEY, None)
    if not isinstance(flow, dict):
        # Возврат без захода: открыт по старой ссылке, в другом браузере или
        # подсунут чужой страницей. Пускать не во что.
        return _refuse(LOGIN, "failed")
    purpose = flow.get("purpose") if flow.get("purpose") in (LOGIN, LINK) else LOGIN
    returned = request.GET.get("state", "")
    # Сравниваем байты: compare_digest на строке с не-ASCII падает TypeError,
    # и подделанный `?state=` превращался бы в 500 вместо отказа.
    expected = str(flow.get("state", "")).encode()
    if not returned or not secrets.compare_digest(returned.encode(), expected):
        log.warning("google callback refused: state mismatch")
        return _refuse(purpose, "failed")
    code = request.GET.get("code", "")
    if request.GET.get("error") or not code:
        # Человек нажал «Отмена» в Google или Google отказал сам.
        return _refuse(purpose, "failed")
    try:
        identity = google_auth.exchange_code(config, code=code)
    except google_auth.GoogleAuthError as failure:
        log.warning("google callback refused: %s", failure)
        return _refuse(purpose, "failed")
    if not secrets.compare_digest(identity.nonce, str(flow.get("nonce", ""))):
        log.warning("google callback refused: nonce mismatch")
        return _refuse(purpose, "failed")

    if purpose == LINK:
        who = get_current_principal(request)
        if who is None:
            return _refuse(LOGIN, "failed")
        try:
            google_login.link_google(who.user_id, identity)
        except google_login.GoogleRefusal as refusal:
            return _refuse(LINK, refusal.code)
        return redirect(reverse("account") + "?google=linked")

    try:
        google_login.login_with_google(request, identity, settings.GOOGLE_HQ_DOMAINS)
    except google_login.GoogleRefusal as refusal:
        return _refuse(LOGIN, refusal.code)
    from .views import safe_next_value

    return redirect(safe_next_value(flow.get("next", "")))
