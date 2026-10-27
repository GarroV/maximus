"""Разговор с Google: адрес согласия, обмен кода, проверка `id_token` (T241, D098).

Перенесено из DECIMUS (`src/web/google_auth.py`, T332, D163, D168, D430) —
поведение то же, код под Django. Кого пускать, здесь не решается: это делает
`web/google_login.py` по заведённым учёткам.

**Своей библиотеки под это не берём.** Нужны один GET-редирект и один POST на
обмен кода — это `urllib.request` из стандартной поставки.

**Подпись `id_token` не проверяется, и это не упущение.** Токен приходит не от
браузера, а прямым ответом token endpoint по TLS, с нашим `client_secret` в
запросе — канал уже доверенный, и Google прямо разрешает в этом случае
пропустить проверку подписи. Что проверить обязательно — `aud`, `iss`, срок,
подтверждённость почты и `nonce`: без них чужое приложение подсунуло бы свой
токен.
"""
from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

GOOGLE_CLIENT_ID_VAR = "GOOGLE_CLIENT_ID"
GOOGLE_CLIENT_SECRET_VAR = "GOOGLE_CLIENT_SECRET"  # noqa: S105 — ИМЯ переменной
GOOGLE_REDIRECT_URI_VAR = "GOOGLE_REDIRECT_URI"
#: Домены Google Workspace компании через запятую. Администратор платформы
#: входит через Google, только если аккаунт из такого домена (DECIMUS D430,
#: F-028). Партнёров не касается: они входят по совпадению с заведённой учёткой.
GOOGLE_HQ_DOMAINS_VAR = "GOOGLE_HQ_DOMAINS"
#: Домены компании по умолчанию. Переменная их заменяет, а не дополняет.
DEFAULT_HQ_DOMAINS = ("dodobrands.io", "dodopizza.com")

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 — адрес, не секрет
#: Неконфиденциальные скоупы: кто это и какая у него почта.
SCOPES = ("openid", "email", "profile")
#: Обе формы издателя, которые Google выдаёт. Список закрытый: принимать
#: произвольного издателя значит не проверять ничего.
ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
#: Запас на расхождение часов между нами и Google.
CLOCK_SKEW_SECONDS = 60
#: Сколько ждём Google: без предела висит поток, обслуживающий человека.
NETWORK_TIMEOUT_SECONDS = 10


class GoogleAuthError(Exception):
    """Разговор с Google не состоялся. Человеку показывается общий отказ."""


@dataclass(frozen=True)
class GoogleSettings:
    """Реквизиты приложения. Живут в окружении площадки, не в репозитории."""

    client_id: str
    client_secret: str
    redirect_uri: str


@dataclass(frozen=True)
class GoogleIdentity:
    """Что Google рассказал о вошедшем. Больше нам от него ничего не нужно."""

    email: str
    #: Постоянный номер аккаунта (`sub`). Почта может смениться, номер — нет.
    subject: str
    #: Домен Google Workspace (`hd`); пусто — личный аккаунт.
    hosted_domain: str = ""
    #: `nonce` из токена — то, что мы послали в уходе к Google.
    nonce: str = ""


def load_google_settings(env: Mapping[str, str]) -> GoogleSettings | None:
    """Реквизиты из окружения. `None` — вход через Google не настроен.

    Не отказ, а «его нет»: стенд без реквизитов работает паролем, как работал.
    Нужны все три: без любого из них Google откажет на полпути, и кнопка,
    ведущая в отказ, хуже отсутствующей.
    """
    client_id = (env.get(GOOGLE_CLIENT_ID_VAR) or "").strip()
    client_secret = (env.get(GOOGLE_CLIENT_SECRET_VAR) or "").strip()
    redirect_uri = (env.get(GOOGLE_REDIRECT_URI_VAR) or "").strip()
    if not (client_id and client_secret and redirect_uri):
        return None
    return GoogleSettings(client_id, client_secret, redirect_uri)


def load_hq_domains(env: Mapping[str, str]) -> frozenset[str]:
    """Домены компании для входа администратора платформы (DECIMUS D430)."""
    raw = (env.get(GOOGLE_HQ_DOMAINS_VAR) or "").strip()
    domains = [part.strip().lower() for part in raw.split(",") if part.strip()]
    return frozenset(domains or DEFAULT_HQ_DOMAINS)


def hq_domain_ok(identity: GoogleIdentity, domains: frozenset[str]) -> bool:
    """Аккаунт из домена компании — по заявке `hd`, а не по хвосту почты.

    `hd` Google выдаёт только аккаунтам Workspace этого домена; личный аккаунт,
    заведённый на адрес с тем же хвостом, `hd` не несёт.
    """
    return bool(identity.hosted_domain) and identity.hosted_domain in domains


def new_token() -> str:
    """Случайная метка: `state` (защита возврата от подделки) и `nonce`."""
    return secrets.token_urlsafe(32)


def authorization_url(settings: GoogleSettings, *, state: str, nonce: str) -> str:
    """Адрес, куда уводим человека на согласие.

    `prompt=select_account`: аккаунтов в браузере бывает несколько, и
    молчаливый вход «не тем» выглядит как отказ доступа.
    """
    params = {
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urllib.parse.urlencode(params)}"


def exchange_code(settings: GoogleSettings, *, code: str) -> GoogleIdentity:
    """Код возврата → кто вошёл. Любая осечка — `GoogleAuthError`."""
    request = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=urllib.parse.urlencode({
            "code": code,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": settings.redirect_uri,
            "grant_type": "authorization_code",
        }).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as answer:  # noqa: S310
            payload = json.loads(answer.read())
    except Exception as failure:  # сеть, разбор, отказ Google — для входа одно и то же
        raise GoogleAuthError(f"обмен кода не удался: {type(failure).__name__}") from failure
    id_token = payload.get("id_token") if isinstance(payload, dict) else None
    if not isinstance(id_token, str) or not id_token:
        raise GoogleAuthError("в ответе Google нет id_token")
    return identity_from_id_token(id_token, client_id=settings.client_id)


def identity_from_id_token(raw: str, *, client_id: str, now: float | None = None) -> GoogleIdentity:
    """Разобрать и проверить `id_token`. Чистая функция — её и тестируем."""
    claims = _payload(raw)
    moment = time.time() if now is None else now
    if claims.get("iss") not in ISSUERS:
        raise GoogleAuthError("токен выписан не Google")
    if claims.get("aud") != client_id:
        raise GoogleAuthError("токен выписан не нашему приложению")
    expires = claims.get("exp")
    if not isinstance(expires, (int, float)) or expires + CLOCK_SKEW_SECONDS < moment:
        raise GoogleAuthError("срок действия токена истёк")
    email = claims.get("email")
    if not isinstance(email, str) or not email.strip():
        raise GoogleAuthError("в токене нет почты")
    # Без `email_verified` вход отдавался бы всякому, кто завёл аккаунт на
    # чужой адрес и не подтвердил его.
    if claims.get("email_verified") is not True:
        raise GoogleAuthError("почта в аккаунте Google не подтверждена")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise GoogleAuthError("в токене нет номера аккаунта Google")
    domain = claims.get("hd")
    nonce = claims.get("nonce")
    return GoogleIdentity(
        email=email.strip().lower(),
        subject=subject.strip(),
        hosted_domain=domain.strip().lower() if isinstance(domain, str) else "",
        nonce=nonce if isinstance(nonce, str) else "",
    )


def _payload(raw: str) -> dict[str, Any]:
    """Средняя часть JWT как словарь. Подпись здесь не разбирается (см. модуль)."""
    parts = raw.split(".")
    if len(parts) != 3:
        raise GoogleAuthError("id_token не похож на JWT")
    middle = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(middle))
    except Exception as failure:
        raise GoogleAuthError("нагрузка id_token не читается") from failure
    if not isinstance(decoded, dict):
        raise GoogleAuthError("нагрузка id_token не объект")
    return decoded
