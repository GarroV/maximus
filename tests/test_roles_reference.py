"""Раздел ролей до эталона «Роли и права» (T233, issue #283).

Проверки идут настоящими страницами, как у соседних файлов экрана ролей.
"""
from __future__ import annotations

import re

from conftest import body, login_as, person_row


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


def _person_page(client, name: str) -> str:
    person = re.search(r"/roles/people/([0-9a-f-]+)/", person_row(
        body(client.get("/roles/people/")), name))
    assert person, f"человека «{name}» нет в списке"
    return body(client.get(f"/roles/people/{person.group(1)}/"))


def test_removing_a_role_says_what_the_person_loses(client, web_env):
    """Прежде «Снять роль» не говорило, чего человек лишится: права видны были
    только на соседней странице, а сложить их с другими ролями человек должен
    был в уме."""
    login_as(client, "admin")
    html = _person_page(client, "Бухгалтер")
    card = re.search(r'<form[^>]*class="card held-role".*?</form>', html, flags=re.S)
    assert card, "нет карточки выданной роли"
    assert "потеряет" in card.group(0)
    assert "Расчёт периода" in card.group(0), "не названо, что уходит вместе с ролью"


def test_granting_a_role_says_what_the_person_gains(client, web_env):
    """Выдача старшей роли называет только новое — то, чего у человека нет."""
    login_as(client, "admin")
    html = _person_page(client, "Бухгалтер")
    offers = re.search(r'<ul class="role-offers">.*?</ul>', html, flags=re.S)
    assert offers, "не сказано, что даст каждая роль"
    admin = re.search(r"<li[^>]*>\s*<b>Администратор сети</b>.*?</li>", offers.group(0), re.S)
    assert admin and "Ведение ролей" in admin.group(0)
    assert "Расчёт периода" not in admin.group(0), "названо то, что у человека уже есть"


def test_a_loss_counts_only_what_no_other_role_of_the_person_gives():
    """Роли складываются (D047): снять роль — потерять лишь то, чего нет в других."""
    from types import SimpleNamespace

    from web.roles_effects import role_effects

    wide = SimpleNamespace(pk="a", code="x", title="Широкая",
                           permissions=["payrun.calculate", "roles.manage"],
                           visible_ledgers=["official", "internal"])
    narrow = SimpleNamespace(pk="b", code="y", title="Узкая",
                             permissions=["payrun.calculate"], visible_ledgers=["official"])
    effects = role_effects(["a", "b"], [wide, narrow])
    assert effects["loses"]["b"] == [], "названо как потеря то, что остаётся в другой роли"
    assert "Ведение ролей" in effects["loses"]["a"]
    assert "Расчёт периода" not in effects["loses"]["a"]
    assert effects["offers"] == []


def test_the_people_list_shows_the_unit_and_the_last_visit(client, web_env):
    """Как в эталоне: у человека видно, какие точки он ведёт и был ли он в системе.

    Без этого список людей не отвечал на главный вопрос администратора — кто
    из выданных доступов живой, а кто висит без дела.
    """
    from core.models import Membership, Unit

    login_as(client, "admin")
    html = body(client.get("/roles/people/"))
    assert "Точка" in html and "Последний вход" in html
    manager = Membership.objects.filter(role__code="manager").exclude(unit_ids=None).first()
    assert manager, "в сиде нет управляющего с точкой"
    unit = Unit.objects.get(pk=manager.unit_ids[0])
    assert unit.title in person_row(html, "Управляющий"), "точка управляющего не названа"
    assert "все точки" in person_row(html, "Бухгалтер")
    assert "не входил" not in person_row(html, "Администратор сети"), (
        "администратор только что вошёл, а список говорит, что он не входил"
    )


def _history_entry(reason: str) -> None:
    """Запись истории — прямо в таблицу: выдача через экран добавила бы роль,
    и соседние тесты увидели бы у человека лишнее."""
    from django.db import connection

    from core.models import Role, Tenant, User

    tenant = Tenant.objects.get(code="rs-dev")
    admin = User.objects.get(username="admin")
    manager = User.objects.get(username="manager")
    role = Role.objects.get(tenant=tenant, code="admin")
    with connection.cursor() as cur:
        cur.execute(
            """insert into access_log (tenant_id, actor_user_id, subject_user_id, action,
                                       role_id, role_title, until, reason)
               values (%s, %s, %s, 'granted', %s, %s, null, %s)""",
            [tenant.pk, admin.pk, manager.pk, role.pk, role.title, reason],
        )


def test_the_access_history_is_exported_as_csv(client, web_env):
    """Выгрузка — все записи, а не последние сто, что на экране."""
    import csv
    import io

    _history_entry("проверка выгрузки")
    login_as(client, "admin")
    answer = client.get("/roles/history/export/")
    assert answer.status_code == 200
    assert answer["Content-Type"].startswith("text/csv")
    assert "attachment" in answer["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(answer.content.decode("utf-8-sig"))))
    assert rows[0][0] == "Когда"
    assert any("проверка выгрузки" in row for row in rows[1:])


def test_a_formula_in_a_reason_does_not_run_in_the_spreadsheet(client, web_env):
    """Причину пишет человек. Файл открывают в Excel, а ячейка, начатая с «=»,
    там не текст, а формула — её отдают текстом."""
    _history_entry("=HYPERLINK(\"http://example.test\")")
    login_as(client, "admin")
    text = client.get("/roles/history/export/").content.decode("utf-8-sig")
    assert "'=HYPERLINK" in text
    assert ",=HYPERLINK" not in text and '"=HYPERLINK' not in text


def test_the_export_is_refused_to_whoever_does_not_lead_roles(client, web_env):
    login_as(client, "manager")
    assert client.get("/roles/history/export/").status_code == 403


def test_the_person_page_says_what_the_roles_do_not_give(client, web_env):
    """Как в эталоне — «чего не даёт»: у бухгалтера нет ведения ролей."""
    login_as(client, "admin")
    html = _person_page(client, "Бухгалтер")
    cannot = re.search(r"Не может:</dt>\s*<dd>([^<]*)</dd>", html)
    assert cannot and "Ведение ролей" in cannot.group(1)
    assert "Расчёт периода" not in cannot.group(1)
