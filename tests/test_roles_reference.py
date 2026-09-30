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


def test_the_rights_matrix_is_a_grid_of_rights_by_roles(client, web_env):
    """Как в эталоне: строки — права по группам, столбцы — роли.

    Прежде на роль была своя строка со списком галочек: чтобы понять, у кого
    есть «Откат периода», приходилось читать все блоки подряд.
    """
    login_as(client, "admin")
    html = body(client.get("/roles/"))
    columns = re.findall(r'<th scope="col"[^>]*data-role="([a-z_]+)"', html)
    assert {"director", "accountant", "manager", "admin"} <= set(columns)
    rows = re.findall(r'<th scope="row"[^>]*>\s*([^<]+?)\s*<', html)
    assert "Откат периода" in rows and "Ведение ролей" in rows
    for group in ("Табель и операционка", "Зарплата и деньги",
                  "Срезы по регистрам учёта", "Правила и справочники", "Доступ и история"):
        assert group in html, f"нет группы «{group}»"
    # Регистры — строки той же сетки, но только на чтение: их задаёт форма роли.
    assert "Внутренний" in rows
    boxes = re.findall(r'<input type="checkbox" name="right:([a-z.]+)" form="rights-', html)
    assert len(boxes) >= len(set(boxes)) * 2, "галочки не разложены по столбцам ролей"
