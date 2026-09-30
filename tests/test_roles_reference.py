"""Раздел ролей до эталона «Роли и права» (T233, issue #283).

Проверки идут настоящими страницами, как у соседних файлов экрана ролей.
"""
from __future__ import annotations

import re

from conftest import body, login_as


def test_role_titles_follow_the_page_language(client, web_env):
    """Нетронутое название роли — слово продукта, и оно переводится.

    Прежде английская страница людей показывала «Бухгалтер» посреди перевода:
    название бралось из базы как есть.
    """
    login_as(client, "admin")
    client.cookies["django_language"] = "en"
    html = body(client.get("/roles/people/"))
    assert "Accountant" in html
    assert "Бухгалтер</span>" not in html, "название роли осталось русским"


def test_a_title_renamed_by_the_partner_is_shown_as_is(client, web_env):
    """Переименованное партнёром — его данные: продукт их не переводит."""
    from core.models import Role, Tenant

    tenant = Tenant.objects.get(code="rs-dev")
    role = Role.objects.get(tenant=tenant, code="accountant")
    before = role.title
    Role.objects.filter(pk=role.pk).update(title="Knjigovođa Beograd")
    try:
        login_as(client, "admin")
        client.cookies["django_language"] = "en"
        html = body(client.get("/roles/people/"))
        assert "Knjigovođa Beograd" in html
        assert not re.search(r">\s*Accountant\s*<", html)
    finally:
        Role.objects.filter(pk=role.pk).update(title=before)
