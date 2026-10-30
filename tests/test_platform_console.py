"""Real access-management flows, including the existing-session and RLS boundaries."""
import pytest

from conftest import body, login_as
from test_platform_admin_screens import platform_admin, spaces_restored  # noqa: F401

pytestmark = pytest.mark.usefixtures("web_env", "spaces_restored", "platform_admin")


def new_space(client, code="console-test"):
    response = client.post("/platform/new/", {
        "title": "Console test", "code": code, "country_code": "RS",
        "base_currency": "RSD", "report_currency": "EUR",
        "admin_username": code, "admin_email": code + "@example.org",
        "admin_password": "test-password-only", "role_code": "admin",
    })
    assert response.status_code == 302
    from core.models import Tenant
    return Tenant.objects.get(code=code)


def test_owner_starts_in_platform_and_partner_cannot_enter(client):
    login_as(client, "admin")
    assert client.get("/").url == "/platform/"
    assert "Управление платформой" in body(client.get("/platform/"))
    login_as(client, "accountant")
    for path in ("/platform/users/", "/platform/history/", "/platform/settings/"):
        assert client.get(path).status_code == 403


def test_new_partner_email_org_and_working_user(client):
    from core.models import LegalEntity, Membership, Role, User
    login_as(client, "admin")
    space = new_space(client)
    assert User.objects.get(username="console-test").email == "console-test@example.org"
    org_url = f"/platform/{space.pk}/organizations/"
    response = client.post(org_url, {"title": "Example company", "tax_number": "123"})
    assert response.status_code == 302
    assert LegalEntity.objects.filter(tenant=space, title="Example company").exists()
    role = Role.objects.get(tenant=space, code="accountant")
    english = body(client.get(f"/platform/{space.pk}/people/new/", HTTP_ACCEPT_LANGUAGE="en"))
    assert "Accountant" in english and "Бухгалтер" not in english
    response = client.post(f"/platform/{space.pk}/people/new/", {
        "email": "new-person@example.org", "full_name": "Example person",
        "role_id": str(role.pk), "reason": "Подключение бухгалтера",
    })
    assert response.status_code == 200
    user = User.objects.get(email="new-person@example.org")
    assert Membership.objects.filter(tenant=space, user_id=user.pk, role=role).exists()
    import re
    password = re.search(r"<code>([^<]+)</code>", body(response))[1]
    assert response.headers["Cache-Control"] == "no-store"
    assert "platform_credentials" not in client.session
    assert password not in body(client.get(f"/platform/{space.pk}/"))
    client.logout()
    response = client.post("/login/", {"username": user.username, "password": password})
    assert response.status_code == 302
    assert client.get("/periods/").status_code == 200
    assert client.get("/platform/").status_code == 403


def test_last_admin_guard_and_cross_space_assignment(client):
    from core.models import Membership, Role, User
    login_as(client, "admin")
    first = new_space(client)
    second = new_space(client, "console-other")
    user = User.objects.get(username="console-test")
    member = Membership.objects.get(tenant=first, user_id=user.pk)
    url = f"/platform/{first.pk}/roles/"
    response = client.post(url, {"action": "revoke", "user_id": str(user.pk),
                                "role_id": str(member.role_id), "reason": "Проверка"})
    assert Membership.objects.filter(pk=member.pk).exists()
    assert "последн" in body(client.get(response.url)).lower()
    role = Role.objects.get(tenant=second, code="accountant")
    response = client.post(f"/platform/{second.pk}/people/new/", {
        "email": "console-test@example.org", "role_id": str(role.pk), "reason": "Проверка",
    })
    assert response.status_code == 400
    assert not Membership.objects.filter(tenant=second, user_id=user.pk).exists()


def test_disable_existing_session_and_protect_platform_admin(client):
    from django.test import Client

    from core.models import User
    login_as(client, "admin")
    new_space(client)
    from core.models import Membership, Role, Tenant
    space = Tenant.objects.get(code="console-test")
    person = User.objects.create_user(username="console-member", password="test-password-only")
    Membership.objects.create(tenant=space, user_id=person.pk,
                              role=Role.objects.get(tenant=space, code="accountant"))
    other = Client()
    assert other.post("/login/", {"username": person.username,
                                "password": "test-password-only"}).status_code == 302
    url = f"/platform/users/{person.pk}/"
    response = client.post(url, {"action": "disable", "reason": "Отключение"})
    assert response.status_code == 302
    assert not User.objects.get(pk=person.pk).is_active
    assert other.get("/periods/").status_code == 302
    response = client.post(f"/platform/users/{User.objects.get(username='admin').pk}/", {
        "action": "disable", "reason": "Проверка",
    })
    assert response.status_code == 400
    assert User.objects.get(pk=User.objects.get(username="admin").pk).is_active


def test_first_admin_requires_email_and_generated_password_stays_out_of_cookie(client):
    from core.models import Tenant, User
    login_as(client, "admin")
    data = {"title": "Generated", "code": "generated", "country_code": "RS",
            "base_currency": "RSD", "report_currency": "EUR", "admin_username": "generated"}
    assert client.post("/platform/new/", data).status_code == 400
    assert not Tenant.objects.filter(code="generated").exists()
    data["admin_email"] = "generated@example.org"
    response = client.post("/platform/new/", data)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "platform_credentials" not in client.session
    import re
    password = re.search(r"<code>([^<]+)</code>", body(response))[1]
    assert User.objects.get(username="generated").check_password(password)



def test_several_roles_are_one_person_and_last_admin_cannot_expire(client):
    from core.models import Membership, Role, User
    login_as(client, "admin")
    space = new_space(client)
    user = User.objects.get(username="console-test")
    admin = Role.objects.get(tenant=space, code="admin")
    accountant = Role.objects.get(tenant=space, code="accountant")
    url = f"/platform/{space.pk}/roles/"
    response = client.post(url, {"user_id": str(user.pk), "role_id": str(accountant.pk),
                                "reason": "Additional assignment"})
    assert response.status_code == 302
    assert Membership.objects.filter(tenant=space, user_id=user.pk).count() == 2
    from web.platform_views import _spaces
    row = next(row for row in _spaces() if row.pk == space.pk)
    assert row.people == 1
    response = client.post(url, {"user_id": str(user.pk), "role_id": str(admin.pk),
                                "until": "2027-12-31", "reason": "Expiry check"})
    assert response.status_code == 400
    assert Membership.objects.get(tenant=space, user_id=user.pk, role=admin).expires_at is None


@pytest.mark.parametrize("action", ["disable", "password", "profile"])
def test_another_platform_administrator_is_protected(client, action):
    from core.models import PlatformAdmin, User
    login_as(client, "admin")
    peer = User.objects.create_user(username="console-peer", password="only-for-test")
    PlatformAdmin.objects.create(user=peer, note="Second test operator")
    old_password, old_email = peer.password, peer.email
    response = client.post(f"/platform/users/{peer.pk}/", {
        "action": action, "reason": "Peer protection check",
        "email": "changed@example.org", "full_name": "Changed peer",
    })
    assert response.status_code == 400
    peer.refresh_from_db()
    assert peer.is_active
    assert peer.password == old_password
    assert peer.email == old_email
