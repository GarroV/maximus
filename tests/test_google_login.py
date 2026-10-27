"""Вход через Google (T241, D098): кого пускать, окно в таблицу учёток, привязка.

Это вход — то есть право доступа, ядро, где ошибка молчалива: чужой аккаунт,
вошедший в учётку партнёра, выглядит как обычный вход. Поэтому проверяется:

1. **Окно политики `users_login_google` (`0279`)** — ролью `app_user`, как
   работает сервис: без настроек не видно ни строки; по почте — ровно
   совпавшая строка без учёта регистра; по номеру аккаунта — ровно его строка;
   пустая почта не совпадает ни с чем.
2. **Кого пускать.** Сначала по номеру аккаунта, потом по почте; совпадение по
   почте записывает номер за учёткой; учётка с другим номером — отказ;
   незнакомая почта — отказ словами, учётка не заводится; администратор
   платформы — только аккаунтом домена компании по `hd`.
3. **Заход и возврат.** Метка `state` и `nonce` сверяются; возврат без захода
   — отказ; адрес возврата после входа — только внутрь продукта.
4. **Привязка** — только своей учётки и только вошедшим; номер, привязанный к
   другой учётке, — отказ.

Сеть к Google не ходит: обмен кода подменяется (`exchange_code`), разбор
`id_token` проверяется отдельно чистой функцией.
"""
from __future__ import annotations

import base64
import json
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlparse

import psycopg
import pytest
from django.test import override_settings

from conftest import as_app_user, body, login_as

CLIENT = "client-id.apps.googleusercontent.com"


def user_id_of(code: str):
    from core.management.commands.seed_dev import det_id

    return det_id("user", code)


@pytest.fixture
def google(web_env):
    """Вход через Google включён: реквизиты заданы, обмен кода подменяется."""
    from web.google_auth import GoogleSettings

    config = GoogleSettings(CLIENT, "secret", "https://front.example/auth/google/callback")
    with override_settings(GOOGLE_AUTH=config):
        yield config


@pytest.fixture
def owner(web_env):
    """Соединение владельцем схемы — готовить учётки; всё вернуть как было."""
    with psycopg.connect(web_env, autocommit=True) as conn:
        try:
            yield conn
        finally:
            conn.execute("update users set email = '', google_sub = null")
            conn.execute("delete from platform_admins where user_id in (select id from users)")


def set_email(owner, code: str, email: str, sub: str | None = None):
    owner.execute(
        "update users set email = %s, google_sub = %s where id = %s",
        (email, sub, user_id_of(code)),
    )


def identity(email="Ana@Example.com", sub="g-111", hd="", nonce=""):
    from web.google_auth import GoogleIdentity

    return GoogleIdentity(email=email.lower(), subject=sub, hosted_domain=hd, nonce=nonce)


def go_through_google(client, monkeypatch, who, *, start="/auth/google/start/", post=False,
                      state=None, nonce=None):
    """Уйти к Google и вернуться с подменённым ответом. Возвращает ответ возврата."""
    from web import google_auth

    gone = client.post(start) if post else client.get(start)
    assert gone.status_code == 302, gone.status_code
    query = parse_qs(urlparse(gone["Location"]).query)
    flow = client.session["google_flow"]
    real_nonce = flow["nonce"]

    def fake_exchange(config, *, code):
        assert code == "the-code"
        return identity(email=who.email, sub=who.subject, hd=who.hosted_domain,
                        nonce=real_nonce if nonce is None else nonce)

    monkeypatch.setattr(google_auth, "exchange_code", fake_exchange)
    returned_state = query["state"][0] if state is None else state
    return client.get("/auth/google/callback", {"state": returned_state, "code": "the-code"})


def logged_in_as(client):
    from django.contrib.auth import SESSION_KEY

    return client.session.get(SESSION_KEY)


# --- 1. окно политики ---------------------------------------------------------


@contextmanager
def login_scope(conn, *, email="", sub=""):
    conn.execute("begin")
    try:
        with as_app_user(conn, None):
            conn.execute(
                "select set_config('app.login_email', %s, true),"
                " set_config('app.login_google_sub', %s, true)",
                (email, sub),
            )
            yield conn
    finally:
        conn.execute("rollback")


def visible(conn) -> list[str]:
    return sorted(row[0] for row in conn.execute("select username from users"))


def test_without_the_scope_no_user_row_is_visible(owner, web_env):
    set_email(owner, "director", "ana@example.com", "g-111")
    with psycopg.connect(web_env) as conn, login_scope(conn):
        assert visible(conn) == []


def test_the_email_scope_opens_exactly_the_matching_row(owner, web_env):
    set_email(owner, "director", "Ana@Example.com")
    set_email(owner, "manager", "boris@example.com")
    with psycopg.connect(web_env) as conn, login_scope(conn, email="ANA@example.COM"):
        assert visible(conn) == ["director"], "почта открыла не ту строку или лишние"


def test_an_empty_email_matches_nothing(owner, web_env):
    """У учёток без почты окна нет: пустая настройка не совпадает с пустой почтой."""
    with psycopg.connect(web_env) as conn, login_scope(conn, email=""):
        assert visible(conn) == []
    with psycopg.connect(web_env) as conn, login_scope(conn, email=" "):
        assert visible(conn) == []


def test_the_subject_scope_opens_exactly_its_row(owner, web_env):
    set_email(owner, "director", "", "g-111")
    set_email(owner, "manager", "", "g-222")
    with psycopg.connect(web_env) as conn, login_scope(conn, sub="g-222"):
        assert visible(conn) == ["manager"]


def test_the_scope_does_not_open_rows_for_writing(owner, web_env):
    """Окно — только на чтение: чужую строку через него не поменять."""
    set_email(owner, "director", "ana@example.com")
    with psycopg.connect(web_env) as conn, login_scope(conn, email="ana@example.com"):
        changed = conn.execute(
            "update users set google_sub = 'stolen' where username = 'director'"
        ).rowcount
        assert changed == 0


# --- 2. кого пускать ----------------------------------------------------------


def test_a_known_email_logs_in_and_binds_the_account(client, google, owner, monkeypatch):
    set_email(owner, "director", "ana@example.com")

    back = go_through_google(client, monkeypatch, identity())

    assert back.status_code == 302 and back["Location"] == "/periods/"
    assert str(logged_in_as(client)) == str(user_id_of("director"))
    sub = owner.execute("select google_sub from users where id = %s",
                        (user_id_of("director"),)).fetchone()[0]
    assert sub == "g-111", "номер аккаунта не записан за учёткой"


def test_the_bound_account_logs_in_even_after_the_email_changed(client, google, owner, monkeypatch):
    set_email(owner, "director", "new@example.com", "g-111")

    go_through_google(client, monkeypatch, identity(email="old@example.com"))

    assert str(logged_in_as(client)) == str(user_id_of("director"))


def test_an_unknown_email_is_refused_in_words(client, google, owner, monkeypatch):
    back = go_through_google(client, monkeypatch, identity(email="stranger@example.com"))

    assert logged_in_as(client) is None
    assert back["Location"] == "/login/?google=unknown"
    page = body(client.get(back["Location"]))
    assert "Этой почты нет среди учётных записей MAXIMUS" in page
    count = owner.execute("select count(*) from users where email = 'stranger@example.com'")
    assert count.fetchone()[0] == 0, "вход через Google завёл учётку"


def test_an_account_bound_to_another_google_is_refused(client, google, owner, monkeypatch):
    set_email(owner, "director", "ana@example.com", "g-999")

    back = go_through_google(client, monkeypatch, identity())

    assert logged_in_as(client) is None
    assert back["Location"] == "/login/?google=other_google"


def test_an_inactive_account_does_not_log_in(client, google, owner, monkeypatch):
    set_email(owner, "director", "ana@example.com")
    owner.execute("update users set is_active = false where id = %s", (user_id_of("director"),))
    try:
        back = go_through_google(client, monkeypatch, identity())
    finally:
        owner.execute("update users set is_active = true where id = %s", (user_id_of("director"),))

    assert logged_in_as(client) is None and back["Location"] == "/login/?google=unknown"


def test_a_platform_admin_with_a_personal_gmail_is_refused(client, google, owner, monkeypatch, caplog):
    """Администратор платформы — только аккаунтом домена компании (`hd`)."""
    set_email(owner, "admin", "boss@dodobrands.io")
    owner.execute("insert into platform_admins (user_id) values (%s)", (user_id_of("admin"),))

    with caplog.at_level("WARNING"):
        back = go_through_google(client, monkeypatch, identity(email="boss@dodobrands.io", hd=""))

    assert logged_in_as(client) is None
    assert back["Location"] == "/login/?google=hq_domain"
    assert "GOOGLE_HQ_DOMAINS" in caplog.text, "в журнале нет подсказки, что дописать"


def test_a_platform_admin_with_the_company_domain_logs_in(client, google, owner, monkeypatch):
    set_email(owner, "admin", "boss@dodobrands.io")
    owner.execute("insert into platform_admins (user_id) values (%s)", (user_id_of("admin"),))

    go_through_google(client, monkeypatch, identity(email="boss@dodobrands.io", hd="dodobrands.io"))

    assert str(logged_in_as(client)) == str(user_id_of("admin"))


def test_a_partner_person_needs_no_company_domain(client, google, owner, monkeypatch):
    """Люди партнёров входят с любой почтой: домен проверяется только у своих."""
    set_email(owner, "manager", "boris@gmail.com")

    go_through_google(client, monkeypatch, identity(email="boris@gmail.com", hd=""))

    assert str(logged_in_as(client)) == str(user_id_of("manager"))


# --- 3. заход и возврат -------------------------------------------------------


def test_a_forged_state_is_refused(client, google, owner, monkeypatch):
    set_email(owner, "director", "ana@example.com")

    back = go_through_google(client, monkeypatch, identity(), state="forged")

    assert logged_in_as(client) is None and back["Location"] == "/login/?google=failed"


def test_a_token_from_another_attempt_is_refused(client, google, owner, monkeypatch):
    set_email(owner, "director", "ana@example.com")

    back = go_through_google(client, monkeypatch, identity(), nonce="other-attempt")

    assert logged_in_as(client) is None and back["Location"] == "/login/?google=failed"


def test_a_return_without_a_start_is_refused(client, google, owner):
    back = client.get("/auth/google/callback", {"state": "x", "code": "y"})

    assert logged_in_as(client) is None and back["Location"] == "/login/?google=failed"


def test_the_state_is_used_once(client, google, owner, monkeypatch):
    """Возврат снимает метку: повтор той же ссылки уже никуда не пускает."""
    set_email(owner, "director", "ana@example.com")
    gone = client.get("/auth/google/start/")
    state = parse_qs(urlparse(gone["Location"]).query)["state"][0]
    client.get("/auth/google/callback", {"state": state, "error": "access_denied"})

    again = client.get("/auth/google/callback", {"state": state, "code": "the-code"})

    assert again["Location"] == "/login/?google=failed" and logged_in_as(client) is None


@pytest.mark.parametrize("target", ["//evil.example/", "/\\evil.example/", "https://evil.example/",
                                    "javascript:alert(1)"])
def test_next_never_leads_outside_the_product(client, google, owner, monkeypatch, target):
    set_email(owner, "director", "ana@example.com")
    from urllib.parse import quote

    back = go_through_google(client, monkeypatch, identity(),
                             start="/auth/google/start/?next=" + quote(target))

    assert back["Location"] == "/periods/", f"{target!r} увёл наружу: {back['Location']}"


def test_next_inside_the_product_is_kept(client, google, owner, monkeypatch):
    set_email(owner, "director", "ana@example.com")

    back = go_through_google(client, monkeypatch, identity(), start="/auth/google/start/?next=/expenses/")

    assert back["Location"] == "/expenses/"


def test_the_consent_address_carries_state_nonce_and_our_redirect(client, google):
    gone = client.get("/auth/google/start/")
    url = urlparse(gone["Location"])
    query = parse_qs(url.query)

    assert url.netloc == "accounts.google.com"
    assert query["redirect_uri"] == [google.redirect_uri]
    assert query["state"][0] == client.session["google_flow"]["state"]
    assert query["nonce"][0] == client.session["google_flow"]["nonce"]


def test_without_settings_there_is_no_button_and_no_route(client, web_env):
    with override_settings(GOOGLE_AUTH=None):
        assert "Войти через Google" not in body(client.get("/login/"))
        assert client.get("/auth/google/start/").status_code == 404
        assert client.get("/auth/google/callback").status_code == 404


def test_with_settings_the_button_is_above_the_password(client, google):
    page = body(client.get("/login/"))

    assert "Войти через Google" in page
    assert page.index("Войти через Google") < page.index('name="password"')


# --- 4. привязка со страницы учётной записи ----------------------------------


def test_a_person_links_his_own_account(client, google, owner, monkeypatch):
    login_as(client, "manager")

    back = go_through_google(client, monkeypatch, identity(email="any@gmail.com", sub="g-333"),
                             start="/account/google/link/", post=True)

    assert back["Location"] == "/account/?google=linked"
    sub = owner.execute("select google_sub from users where id = %s",
                        (user_id_of("manager"),)).fetchone()[0]
    assert sub == "g-333"


def test_an_account_linked_to_someone_else_is_refused(client, google, owner, monkeypatch):
    set_email(owner, "director", "", "g-333")
    login_as(client, "manager")

    back = go_through_google(client, monkeypatch, identity(sub="g-333"),
                             start="/account/google/link/", post=True)

    assert back["Location"] == "/account/?google=taken"
    assert "уже привязан к другой учётной записи" in body(client.get(back["Location"]))
    sub = owner.execute("select google_sub from users where id = %s",
                        (user_id_of("manager"),)).fetchone()[0]
    assert sub is None


def test_linking_needs_a_login_and_a_post(client, google):
    assert client.get("/account/google/link/").status_code == 405
    assert client.post("/account/google/link/")["Location"].startswith("/login/")


def test_unlink_drops_the_account(client, google, owner):
    set_email(owner, "manager", "", "g-333")
    login_as(client, "manager")

    client.post("/account/google/unlink/")

    sub = owner.execute("select google_sub from users where id = %s",
                        (user_id_of("manager"),)).fetchone()[0]
    assert sub is None


# --- разбор id_token ----------------------------------------------------------


def token(**claims) -> str:
    base = {"iss": "https://accounts.google.com", "aud": CLIENT, "exp": time.time() + 600,
            "email": "Ana@Example.com", "email_verified": True, "sub": "g-1", "nonce": "n"}
    base.update(claims)
    middle = base64.urlsafe_b64encode(json.dumps(base).encode()).decode().rstrip("=")
    return f"h.{middle}.s"


def test_a_valid_token_gives_the_identity():
    from web.google_auth import identity_from_id_token

    who = identity_from_id_token(token(hd="DodoBrands.io"), client_id=CLIENT)

    assert (who.email, who.subject, who.hosted_domain, who.nonce) == (
        "ana@example.com", "g-1", "dodobrands.io", "n")


@pytest.mark.parametrize("claims", [
    {"email_verified": False}, {"email_verified": "true"}, {"aud": "other-app"},
    {"iss": "https://evil.example"}, {"exp": time.time() - 3600}, {"sub": ""}, {"email": ""},
])
def test_a_bad_token_is_refused(claims):
    from web.google_auth import GoogleAuthError, identity_from_id_token

    with pytest.raises(GoogleAuthError):
        identity_from_id_token(token(**claims), client_id=CLIENT)


def test_all_three_variables_are_needed():
    from web.google_auth import load_google_settings

    full = {"GOOGLE_CLIENT_ID": "a", "GOOGLE_CLIENT_SECRET": "b", "GOOGLE_REDIRECT_URI": "c"}
    assert load_google_settings(full) is not None
    for missing in full:
        assert load_google_settings({k: v for k, v in full.items() if k != missing}) is None


def test_the_hq_domains_variable_replaces_the_default():
    from web.google_auth import DEFAULT_HQ_DOMAINS, load_hq_domains

    assert load_hq_domains({}) == frozenset(DEFAULT_HQ_DOMAINS)
    assert load_hq_domains({"GOOGLE_HQ_DOMAINS": " Example.com , "}) == {"example.com"}
