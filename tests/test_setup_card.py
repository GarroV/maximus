"""Карточка «Настройка партнёра» на списке месяцев (D100).

Владелец 12.11.2026: нового партнёра надо «прям за ручку провести». Проверяется
не наличие слов на странице, а **честность состояния**: шаг зелёный, только когда
данные в базе есть, и главная кнопка ведёт на первый несделанный. Пустого
партнёра заводят настоящим путём — экраном платформы, — а не вставкой в таблицы.
"""
from __future__ import annotations

import pytest

from conftest import body, login_as, period_url, wipe_payruns
from test_platform_admin_screens import platform_admin, spaces_restored  # noqa: F401

pytestmark = pytest.mark.usefixtures("web_env", "spaces_restored", "platform_admin")

PASSWORD = "test-password-only"
TITLE = "Настройка партнёра"


def empty_partner(client, code="setup-card"):
    """Новый партнёр без единого справочника; в него входит его первый человек.

    Страна HR: календаря на неё в сиде нет, значит шаг календаря честно пуст.
    """
    login_as(client, "admin")
    response = client.post("/platform/new/", {
        "title": "Setup card", "code": code, "country_code": "HR",
        "base_currency": "EUR", "report_currency": "EUR",
        "admin_username": code, "admin_email": code + "@example.org",
        "admin_password": PASSWORD, "role_code": "admin",
    })
    assert response.status_code == 302, response.status_code
    from core.models import Tenant
    space = Tenant.objects.get(code=code)
    client.post("/logout/")
    return space


def sign_in(client, username):
    response = client.post("/login/", {"username": username, "password": PASSWORD})
    assert response.status_code == 302


def test_empty_partner_sees_six_undone_steps_and_the_button_leads_to_the_legal_entity(client):
    empty_partner(client)
    sign_in(client, "setup-card")

    response = client.get("/periods/")
    card = response.context["setup"]

    assert [step["key"] for step in card["steps"]] == [
        "legal_entity", "unit", "group", "employee", "calendar", "period",
    ]
    assert [step["done"] for step in card["steps"]] == [False] * 6, card["steps"]
    assert card["done_count"] == 0 and card["next_key"] == "legal_entity"
    html = body(response)
    assert TITLE in html and "Готово 0 из 6" in html
    # Главная кнопка одна и ведёт на юрлицо.
    assert html.count('class="btn primary" href="/directory/legal-entities/new/"') == 1
    assert html.count("btn primary") == html.count('class="btn primary" href="/directory/')


def test_a_step_turns_green_from_the_data_and_the_button_moves_on(client):
    space = empty_partner(client)
    login_as(client, "admin")
    assert client.post(
        f"/platform/{space.pk}/organizations/", {"title": "Example company", "tax_number": "1"},
    ).status_code == 302
    client.post("/logout/")
    sign_in(client, "setup-card")

    card = client.get("/periods/").context["setup"]

    assert [step["done"] for step in card["steps"]][:2] == [True, False]
    assert card["done_count"] == 1 and card["next_key"] == "unit"
    assert 'class="btn primary" href="/directory/units/new/"' in body(client.get("/periods/"))


def test_a_country_calendar_that_already_exists_makes_its_step_green(client):
    """Календарь общий для страны (#100): чужой, но уже заведённый, шаг закрывает."""
    from decimal import Decimal
    from datetime import date

    from core.models import Calendar

    empty_partner(client)
    Calendar.objects.create(
        country_code="HR", period=date(2031, 1, 1), norm_hours=Decimal("168"), working_days=21,
    )
    try:
        sign_in(client, "setup-card")
        steps = {s["key"]: s["done"] for s in client.get("/periods/").context["setup"]["steps"]}
        assert steps["calendar"] is True and steps["legal_entity"] is False
    finally:
        Calendar.objects.filter(country_code="HR").delete()


def test_a_fully_set_up_partner_gets_one_line_until_the_first_month_is_approved(client):
    """В сиде всё заведено, но ни один месяц не утверждён: вместо шагов одна строка."""
    login_as(client, "accountant")
    response = client.get("/periods/")
    card = response.context["setup"]
    assert card["all_done"] and card["done_count"] == 6
    html = body(response)
    assert "Настройка завершена — внесите часы первого месяца." in html
    assert TITLE not in html


def test_after_the_first_approved_month_there_is_no_card(client, web_env):
    """Месяц утверждают настоящим путём: посчитали, утвердили — карточка уходит."""
    wipe_payruns(web_env)
    login_as(client, "director")
    url = period_url(client)
    try:
        assert client.get("/periods/").context["setup"] is not None
        assert client.post(url + "calculate/", follow=True).status_code == 200
        assert client.post(url + "approve/", follow=True).status_code == 200

        response = client.get("/periods/")
        assert response.context["setup"] is None
        assert "Настройка завершена" not in body(response)
    finally:
        wipe_payruns(web_env)


def test_a_person_who_cannot_manage_directories_does_not_see_the_card(client):
    empty_partner(client)
    login_as(client, "manager")
    response = client.get("/periods/")
    assert response.context["setup"] is None
    assert TITLE not in body(response)


def test_the_missing_calendar_on_the_month_page_is_a_link_for_who_can_manage_it(client):
    from core.models import Period

    login_as(client, "director")
    assert client.post("/periods/open/", {"month": "2031-01"}).status_code == 302
    try:
        period = Period.objects.get(period="2031-01-01")
        page = body(client.get(f"/periods/{period.pk}/"))
        assert 'href="/directory/calendar/new/"' in page

        # Календарь июня в сиде есть: у месяца с нормой ссылки нет.
        june = Period.objects.exclude(pk=period.pk).get(period="2026-06-01")
        assert 'href="/directory/calendar/new/"' not in body(client.get(f"/periods/{june.pk}/"))

        login_as(client, "manager")
        page = body(client.get(f"/periods/{period.pk}/"))
        assert 'href="/directory/calendar/new/"' not in page
    finally:
        Period.objects.filter(period="2031-01-01").delete()
