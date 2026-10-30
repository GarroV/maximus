"""Platform metadata and access operations; never a customer-data context switch."""
from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.utils.translation import gettext as _

from .models import Membership, PlatformEvent, Role, Tenant, User
from .spaces import SpaceRefused, is_platform_admin


def require_admin(actor):
    if not is_platform_admin(actor):
        raise SpaceRefused(_("Управлять платформой может только администратор платформы."))


def protected_account(user_id):
    # Bootstrap rows stay self-only under RLS. Ask only for the protection bit;
    # the database independently checks the caller's platform right.
    with connection.cursor() as cursor:
        cursor.execute("select app_platform_account_protected(%s)", [user_id])
        return cursor.fetchone()[0]


def email_value(value):
    value = (value or "").strip().lower()
    try:
        validate_email(value)
    except ValidationError as exc:
        raise SpaceRefused(_("Укажите действительный адрес электронной почты.")) from exc
    return value


def event(actor, action, target, *, tenant=None, reason=""):
    require_admin(actor)
    PlatformEvent.objects.create(
        actor_user_id=actor, action=action, target=str(target), tenant=tenant,
        reason=(reason or "").strip(),
    )


def expiry(value):
    if not value:
        return None
    try:
        until = date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise SpaceRefused(_("Укажите срок в формате ГГГГ-ММ-ДД.")) from exc
    if until < date.today():
        raise SpaceRefused(_("Срок доступа не может быть в прошлом."))
    return until


def lock_email(email):
    with connection.cursor() as cursor:
        cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", [email])


def lock_person(user_id):
    # Every console membership/activation operation uses the same order, even
    # when the user has no memberships yet. This closes the assign/disable race.
    with connection.cursor() as cursor:
        cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                       ["platform-user:" + str(user_id)])


def _reason(value):
    value = (value or "").strip()
    if not value:
        raise SpaceRefused(_("Укажите причину изменения доступа."))
    return value


def _guard_last(tenant, member, *, removing=False, until=None):
    if "roles.manage" not in (member.role.permissions or []):
        return
    if member.expires_at and member.expires_at < date.today():
        return
    if not User.objects.filter(pk=member.user_id, is_active=True).exists():
        return
    others = Membership.objects.filter(
        tenant=tenant, expires_at__isnull=True,
    ).exclude(pk=member.pk).select_related("role")
    active = set(User.objects.filter(is_active=True).values_list("pk", flat=True))
    has_other = any(m.user_id in active and "roles.manage" in (m.role.permissions or [])
                    for m in others)
    if not has_other and (removing or until is not None):
        raise SpaceRefused(_("Нельзя убрать последнего действующего бессрочного администратора. "
                           "Сначала назначьте другого человека с правом управления доступом."))


@transaction.atomic
def assign(actor, tenant_id, user_id, role_id, *, unit_ids=None, until=None,
           reason="", remove=False):
    require_admin(actor)
    reason = _reason(reason)
    lock_person(user_id)
    tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
    user = User.objects.select_for_update().get(pk=user_id)
    if not user.is_active and not remove:
        raise SpaceRefused(_("Учётная запись отключена. Сначала включите её."))
    if Membership.objects.filter(user_id=user.pk).exclude(tenant=tenant).exists():
        raise SpaceRefused(_("У человека уже есть другое рабочее пространство. "
                           "Сначала снимите прежнее членство."))
    role = Role.objects.filter(pk=role_id, tenant=tenant).first()
    if role is None:
        raise SpaceRefused(_("Такой роли в этом пространстве нет."))
    member = Membership.objects.filter(tenant=tenant, user_id=user.pk, role=role).first()
    if member:
        _guard_last(tenant, member, removing=remove, until=until)
    if remove:
        if member is None:
            raise SpaceRefused(_("Этой роли у человека нет."))
        member.delete()
        action = _("Роль снята")
    else:
        Membership.objects.update_or_create(
            tenant=tenant, user_id=user.pk, role=role,
            defaults={"unit_ids": unit_ids, "expires_at": until},
        )
        action = _("Доступ изменён") if member else _("Роль выдана")
    event(actor, action, f"{user.full_name or user.username}: {role.title}",
          tenant=tenant, reason=reason)


@transaction.atomic
def add_person(actor, tenant_id, *, email, full_name, password, role_id,
               unit_ids=None, until=None, reason=""):
    require_admin(actor)
    email = email_value(email)
    # Serialise identity lookup/create without introducing another identity namespace.
    lock_email(email)
    matches = list(User.objects.filter(email__iexact=email).order_by("pk")[:2])
    if len(matches) > 1:
        raise SpaceRefused(_("У этой почты несколько учётных записей. Сначала уточните их почту."))
    created = not matches
    if matches:
        user = matches[0]
    else:
        if User.objects.filter(username=email).exists():
            raise SpaceRefused(_(
                "Этот логин уже занят. Уточните почту существующей учётной записи."))
        user = User.objects.create_user(
            username=email, email=email, full_name=(full_name or "").strip() or email,
            password=password,
        )
    assign(actor, tenant_id, user.pk, role_id, unit_ids=unit_ids, until=until, reason=reason)
    return user, created


@transaction.atomic
def set_active(actor, user_id, enabled, reason):
    require_admin(actor)
    reason = _reason(reason)
    lock_person(user_id)
    # Shared order with assignment: identity advisory lock, then tenants, then row.
    tenant_ids = Membership.objects.filter(user_id=user_id).values_list("tenant_id", flat=True)
    tenants = list(Tenant.objects.select_for_update().filter(pk__in=tenant_ids).order_by("pk"))
    user = User.objects.select_for_update().get(pk=user_id)
    if not enabled:
        if protected_account(user.pk):
            raise SpaceRefused(_(
                "Платформенного администратора нельзя отключить из этого кабинета."))
        for tenant in tenants:
            mine = Membership.objects.filter(tenant=tenant, user_id=user.pk).select_related("role")
            # Exclude all assignments of the disabled person, not merely one role.
            others = Membership.objects.filter(tenant=tenant, expires_at__isnull=True).exclude(
                user_id=user.pk,
            ).select_related("role")
            active = set(User.objects.filter(is_active=True).values_list("pk", flat=True))
            if any("roles.manage" in (m.role.permissions or []) for m in mine) and not any(
                m.user_id in active and "roles.manage" in (m.role.permissions or []) for m in others
            ):
                raise SpaceRefused(_("Нельзя отключить последнего администратора пространства."))
    user.is_active = enabled
    user.save(update_fields=["is_active"])
    event(actor, _("Учётная запись включена") if enabled else _("Учётная запись отключена"),
          user.full_name or user.username, reason=reason)
