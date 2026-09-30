"""Платформенная админка на экране: пространства, их люди и роли (D065, issue #193).

Пара к `test_platform_admin_access`: там проверяется, что запрет держит база,
здесь — что продуктом можно пользоваться и что отказ приходит словами.

Половина пары, проверенная за обе, — ровно тот способ, которым в этом проекте
уже прожил незамеченным дефект видимости регистров: экран показывал правильное,
а запись мимо экрана проходила.
"""
from __future__ import annotations

import pytest

from conftest import body, login_as

pytestmark = pytest.mark.usefixtures("web_env")


@pytest.fixture
def platform_admin(web_env):
    """Администратор сети получает право платформы и теряет его после теста."""
    from django.db import connection

    from core.models import User

    user = User.objects.get(username="admin")
    with connection.cursor() as cur:
        cur.execute(
            "insert into platform_admins (user_id, note) values (%s, 'тест экрана') "
            "on conflict (user_id) do nothing",
            (str(user.pk),),
        )
    yield user
    with connection.cursor() as cur:
        cur.execute("delete from platform_admins where user_id = %s", (str(user.pk),))


@pytest.fixture
def spaces_restored(web_env):
    """Пространства, заведённые тестом, убираются за собой.

    База общая на сессию: оставленный партнёр всплыл бы в чужих проверках как
    лишняя строка списка — и они бы покраснели не по своей вине.
    """
    from core.models import Membership, Role, Tenant, User

    before = set(Tenant.objects.values_list("id", flat=True))
    users_before = set(User.objects.values_list("id", flat=True))
    yield
    # Порядок обязателен: роль защищена от удаления ссылкой членства, а тенант —
    # ссылкой роли (`on_delete=PROTECT`). Это не мешает уборке, а показывает, что
    # схема бережёт закрытые месяцы: удалить партнёра «заодно» она не даст.
    stale = Tenant.objects.exclude(id__in=before)
    Membership.objects.filter(tenant__in=stale).delete()
    Role.objects.filter(tenant__in=stale).delete()
    stale.delete()
    User.objects.exclude(id__in=users_before).delete()


# --- кому открыто ------------------------------------------------------------


@pytest.mark.parametrize("who", ["director", "accountant", "manager", "admin"])
def test_partner_roles_get_a_refusal_in_words(client, who):
    """Ни одна роль партнёра платформенной админки не открывает.

    Отказом, а не 404: страница, притворившаяся несуществующей, читается как
    поломка продукта, и человек ищет, что сломалось, вместо того чтобы понять,
    что дверь не его.
    """
    from core.models import Tenant

    login_as(client, who)
    # Каждая страница админки, а не только первая (T225): форма заведения и
    # выдача роли стали отдельными адресами, и у каждого своя развилка входа.
    tenant = Tenant.objects.get(code="rs-dev").pk
    for url in ("/platform/", "/platform/new/", f"/platform/{tenant}/",
                f"/platform/{tenant}/roles/"):
        response = client.get(url)
        assert response.status_code == 403, f"{url} открыт роли {who}"
        assert "администратор платформы" in body(response).lower(), url


def test_platform_admin_sees_the_list_of_spaces(client, platform_admin):
    """Список пространств — первый экран, и в нём видно всех партнёров."""
    login_as(client, "admin")
    html = body(client.get("/platform/"))
    assert "Пространства" in html
    assert "Dodo Serbia" in html or "Serbia" in html


# --- заведение пространства --------------------------------------------------


def test_creating_a_space_gives_a_working_partner(client, platform_admin, spaces_restored):
    """Заведённое пространство сразу пригодно к работе: роли есть, человек входит."""
    from core.models import Membership, Role, Tenant

    login_as(client, "admin")
    response = client.post("/platform/new/", {
        "title": "Dodo Hrvatska", "code": "hr-dodo",
        "country_code": "HR", "base_currency": "EUR", "report_currency": "EUR",
        "admin_username": "hr-boss", "admin_full_name": "Первый человек",
        "admin_password": "very-secret-1", "role_code": "admin",
    })
    assert response.status_code == 302, "после заведения должно перекинуть внутрь пространства"

    tenant = Tenant.objects.filter(code="hr-dodo").first()
    assert tenant is not None, "пространство не появилось"
    assert Role.objects.filter(tenant=tenant).count() == 4, (
        "у пространства должны быть все роли продукта — иначе человека некуда пустить"
    )
    assert Membership.objects.filter(tenant=tenant).count() == 1

    # Роль заведена со снимком формы: без него доставка (T169) сочла бы её
    # правленной партнёром и перестала бы до неё доезжать.
    for role in Role.objects.filter(tenant=tenant):
        assert role.shipped_shape, f"у роли {role.code} нет снимка формы"


def test_a_duplicate_code_is_refused_in_words(client, platform_admin, spaces_restored):
    """Повтор кода — отказ с объяснением, а не ошибка базы на весь экран."""
    login_as(client, "admin")
    client.post("/platform/new/", {
        "title": "Первый", "code": "dup-code", "country_code": "RS",
        "base_currency": "RSD", "report_currency": "EUR",
        "admin_username": "dup-one", "admin_password": "secret-1",
    })
    response = client.post("/platform/new/", {
        "title": "Второй", "code": "dup-code", "country_code": "RS",
        "base_currency": "RSD", "report_currency": "EUR",
        "admin_username": "dup-two", "admin_password": "secret-2",
    })
    # Отказ — на странице самой формы, с набранным, а не на экране списка (T225).
    assert response.status_code == 400
    html = body(response)
    assert "уже есть" in html, "повтор кода должен объясняться словами"
    assert 'value="Второй"' in html, "набранное потерялось — вводить заново"
    assert "secret-2" not in html, "пароль вписан обратно в страницу"


def test_a_space_without_a_first_person_is_refused(client, platform_admin, spaces_restored):
    """Пространство без человека — оболочка, в которую некому войти."""
    from core.models import Tenant

    login_as(client, "admin")
    response = client.post("/platform/new/", {
        "title": "Ничей", "code": "nobody", "country_code": "RS",
        "base_currency": "RSD", "report_currency": "EUR",
        "admin_username": "", "admin_password": "",
    })
    assert not Tenant.objects.filter(code="nobody").exists(), (
        "пространство завелось без первого человека — войти в него некому"
    )
    assert "логин" in body(response).lower()


# --- люди и роли внутри пространства -----------------------------------------


def test_inside_a_space_people_and_their_roles_are_visible(client, platform_admin):
    """Углубившись в пространство, видно его сотрудников — как просил владелец."""
    from core.models import Tenant

    tenant = Tenant.objects.get(code="rs-dev")
    login_as(client, "admin")
    html = body(client.get(f"/platform/{tenant.pk}/"))
    assert "Люди пространства" in html
    assert "Бухгалтер" in html, "роли людей должны быть видны"


def test_a_role_can_be_granted_and_revoked(client, platform_admin, spaces_restored):
    """Выдача и снятие роли — то, ради чего экран и заводился."""
    from core.models import Membership, Role, Tenant, User

    tenant = Tenant.objects.get(code="rs-dev")
    person = User.objects.create_user(username="new-hand", password="secret-1")
    role = Role.objects.filter(tenant=tenant, code="manager").first()

    login_as(client, "admin")
    client.post(f"/platform/{tenant.pk}/roles/", {
        "action": "grant", "user_id": str(person.pk), "role_id": str(role.pk),
    })
    assert Membership.objects.filter(tenant=tenant, user_id=person.pk, role=role).exists()

    client.post(f"/platform/{tenant.pk}/roles/", {
        "action": "revoke", "user_id": str(person.pk), "role_id": str(role.pk),
    })
    assert not Membership.objects.filter(tenant=tenant, user_id=person.pk, role=role).exists()


def test_a_role_of_another_space_cannot_be_granted(client, platform_admin, spaces_restored):
    """Подмена идентификатора в форме не выдаёт роль соседнего пространства.

    Фильтр по тенанту стоит в самом запросе роли, а не проверяется после — иначе
    выдача чужой роли зависела бы от того, не забыли ли написать проверку.
    """
    from core.models import Membership, Role, Tenant, User

    home = Tenant.objects.get(code="rs-dev")
    login_as(client, "admin")

    # Второе пространство заводится тем же экраном: в базе разработки партнёр
    # один, а проверять изоляцию не на чем — значит соседа надо создать.
    client.post("/platform/new/", {
        "title": "Сосед", "code": "neighbour", "country_code": "RS",
        "base_currency": "RSD", "report_currency": "EUR",
        "admin_username": "neighbour-boss", "admin_password": "secret-1",
    })
    other = Tenant.objects.get(code="neighbour")
    alien_role = Role.objects.filter(tenant=other).first()
    assert alien_role is not None, "у соседнего пространства должны быть роли"
    person = User.objects.create_user(username="curious", password="secret-1")
    client.post(f"/platform/{home.pk}/roles/", {
        "action": "grant", "user_id": str(person.pk), "role_id": str(alien_role.pk),
    })
    assert not Membership.objects.filter(user_id=person.pk).exists(), (
        "выдана роль чужого пространства"
    )


# --- статистика по пространствам ---------------------------------------------


def test_the_list_shows_life_of_each_space(client, platform_admin):
    """Пустое пространство и живое в списке различимы — этого владелец и просил.

    «В админке также в будущем нужна будет статистика по пространствам.
    сотрудники, заходы, не знаю» — здесь то, что платформенная админка вправе
    видеть: сколько людей, сколько действующих, когда заходили. Дальше начинаются
    финансы партнёра, и туда платформенное право не открыто нарочно (`0261`).
    """
    login_as(client, "admin")
    html = body(client.get("/platform/"))
    assert "Людей" in html and "Действующих" in html and "Последний вход" in html


def test_a_space_nobody_entered_says_so_in_words(client, platform_admin, spaces_restored):
    """Никто не заходил — сказано словами, а не пустой ячейкой.

    Пустая ячейка в колонке дат читается как сбой выборки, и человек идёт искать
    поломку вместо того, чтобы увидеть ответ: партнёра завели и забыли.
    """
    login_as(client, "admin")
    client.post("/platform/new/", {
        "title": "Тихий", "code": "quiet", "country_code": "RS",
        "base_currency": "RSD", "report_currency": "EUR",
        "admin_username": "quiet-boss", "admin_password": "secret-1",
    })
    html = body(client.get("/platform/"))
    assert "не входили" in html


def test_the_counters_are_honest(client, platform_admin, spaces_restored):
    """Числа считаются, а не показываются нулями: свежий партнёр — один человек."""
    from core.models import Tenant

    login_as(client, "admin")
    client.post("/platform/new/", {
        "title": "Считалка", "code": "counted", "country_code": "RS",
        "base_currency": "RSD", "report_currency": "EUR",
        "admin_username": "counted-boss", "admin_password": "secret-1",
    })
    space = Tenant.objects.get(code="counted")
    from web.platform_views import _spaces

    row = next(item for item in _spaces() if item.pk == space.pk)
    assert row.people == 1, "у нового партнёра ровно один человек — тот, кого мы завели"
    assert row.active == 1, "он действующий: отключать его никто не просил"
    assert row.last_seen is None, "он ещё не входил"


# --- одна страница — одна функция (T225, D081) ------------------------------


def test_the_list_leads_to_the_form_and_the_form_is_its_own_page(client, platform_admin):
    """Список пространств формы не держит; форма — по кнопке, своей страницей."""
    login_as(client, "admin")
    listing = body(client.get("/platform/"))
    assert 'href="/platform/new/"' in listing, "со списка нет дороги к заведению"
    assert 'name="admin_password"' not in listing, "форма заведения осталась на списке"
    form = client.get("/platform/new/")
    assert form.status_code == 200
    assert 'name="admin_password"' in body(form)


def test_granting_a_role_is_its_own_page(client, platform_admin):
    """Люди пространства — одна страница, выдача роли — другая."""
    from core.models import Tenant

    tenant = Tenant.objects.get(code="rs-dev")
    login_as(client, "admin")
    people = body(client.get(f"/platform/{tenant.pk}/"))
    assert f'href="/platform/{tenant.pk}/roles/"' in people, "нет дороги к выдаче роли"
    assert 'value="grant"' not in people, "форма выдачи осталась на странице людей"
    grant = client.get(f"/platform/{tenant.pk}/roles/")
    assert grant.status_code == 200
    assert 'value="grant"' in body(grant)


def test_a_garbled_person_id_is_refused_in_words(client, platform_admin):
    """Не идентификатор в поле человека — ответ словами, а не ошибка сервера."""
    from core.models import Role, Tenant

    tenant = Tenant.objects.get(code="rs-dev")
    role = Role.objects.filter(tenant=tenant).first()
    login_as(client, "admin")
    response = client.post(f"/platform/{tenant.pk}/roles/", {
        "action": "grant", "user_id": "не-идентификатор", "role_id": str(role.pk),
    })
    # 400, а не 404: страница формы есть, неверно то, что в неё вписали. И
    # только так отказ остаётся рядом с формой — 404 со страницей при отладке
    # подменяется общей страницей продукта (`web.errors`).
    assert response.status_code == 400
    assert "Такого человека нет" in body(response)
