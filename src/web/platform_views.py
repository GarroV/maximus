"""Platform console: workspaces, organizations, accounts, assignments and history.

The separate platform right (D065/D099) opens administrative metadata only.
Tenant financial isolation remains in RLS; narrow additions live in migration0280.
Generated credentials appear only in a no-store POST response, never in a session.
"""
from __future__ import annotations

import secrets
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from core import platform
from core.models import LegalEntity, Membership, PlatformEvent, Role, Tenant, Unit, User
from core.roles import DEFAULT_TITLES, ROLE_ORDER
from core.spaces import SpaceRefused, create_space, is_platform_admin

from .principal import get_current_principal
from .roles_display import role_choice, shown_role_title
from .roles_views import membership_units


def _refuse(request):
    """Экран для того, кому платформенная админка не положена.

    Отказ словами, а не 404: страница, притворившаяся несуществующей, читается
    как поломка продукта. Человек должен понять, что дверь есть, но не его.
    """
    return render(
        request,
        "web/platform/denied.html",
        {
            "message": _(
                "Пространствами партнёров управляет администратор платформы. "
                "Это право не входит ни в одну роль партнёра и выдаётся вне продукта."
            )
        },
        status=403,
    )


def _spaces():
    """Пространства со статистикой — то, из чего состоит первый экран.

    Владелец просил в админке статистику: «сотрудники, заходы, не знаю. еще
    что-то». Здесь ровно то, что платформенная админка **вправе** видеть: люди и
    их входы. Дальше начинаются финансы партнёра, и туда платформенное право не
    открыто нарочно (миграция `0261`) — счётчик посчитанных периодов пришлось бы
    брать из таблиц, которых администратор платформы не видит.

    Пустое пространство и живое выглядят в списке одинаково, а разница между ними
    — «партнёра подключили» и «завели и забыли». Отсюда три числа: сколько людей,
    сколько из них действующих и когда в пространство заходили в последний раз.

    Три запроса на весь список, а не по запросу на строку: `Membership.user_id` —
    голый uuid без связи с `users` (так задумано, чтобы схема не зависела от
    того, чем окажется учётка), поэтому люди подбираются отдельно и складываются
    здесь. Партнёров десятки, людей сотни — перебор в цикле стоил бы запроса на
    каждого.
    """
    spaces = list(
        Tenant.objects.annotate(
            people=Count("membership__user_id", distinct=True),
        ).order_by("title")
    )

    who_where: dict = {}
    for tenant_id, user_id in Membership.objects.values_list("tenant_id", "user_id"):
        who_where.setdefault(tenant_id, set()).add(user_id)

    everyone = {
        user.pk: user
        for user in User.objects.filter(
            pk__in={user_id for people in who_where.values() for user_id in people}
        ).only("id", "is_active", "last_login")
    }

    for space in spaces:
        people = [everyone[uid] for uid in who_where.get(space.pk, ()) if uid in everyone]
        space.active = sum(1 for person in people if person.is_active)
        # Пусто — ответ, а не пробел: в пространство не входил никто. Показывается
        # словами, а не пустой ячейкой, иначе читается как сбой выборки.
        visits = [person.last_login for person in people if person.last_login]
        space.last_seen = max(visits) if visits else None

    return spaces


def _role_options(selected: str = "admin") -> list:
    """Роли продукта, а не роли какого-то пространства: пространства ещё нет.
    Названия — те же, что лягут в базу при заведении."""
    return [
        {"code": code, "title": shown_role_title(DEFAULT_TITLES[code], code),
         "selected": code == selected}
        for code in ROLE_ORDER
    ]


@login_required
def index(request):
    """Список пространств. Заведение нового — своей страницей (`space_create`)."""
    who = get_current_principal(request)
    if not is_platform_admin(who.user_id if who else None):
        return _refuse(request)

    return render(
        request,
        "web/platform/index.html",
        {
            "spaces": [s for s in _spaces() if not request.GET.get("q") or
                       request.GET["q"].casefold() in (s.title + " " + s.code).casefold()],
            "notice": request.session.pop("platform_notice", ""),
            "error": request.session.pop("platform_error", ""),
        },
    )


# Поля формы заведения, которые возвращаются в неё после отказа. Пароля среди
# них нет намеренно: пароль, вписанный сервером обратно в страницу, оседает в
# её коде и в истории браузера.
_SPACE_FIELDS = (
    "title", "code", "country_code", "base_currency", "report_currency",
    "admin_username", "admin_full_name", "admin_email",
)
_SPACE_DEFAULTS = {"country_code": "RS", "base_currency": "RSD", "report_currency": "EUR"}


def _space_form(request, *, error: str = "", status: int = 200):
    filled = dict(_SPACE_DEFAULTS)
    if request.method == "POST":
        filled.update({key: (request.POST.get(key) or "").strip() for key in _SPACE_FIELDS})
    return render(
        request,
        "web/platform/new.html",
        {
            "filled": filled,
            "role_options": _role_options(request.POST.get("role_code") or "admin"),
            "error": error,
        },
        status=status,
    )


@login_required
def space_create(request):
    """Завести пространство и первого его человека: форма и её отправка."""
    who = get_current_principal(request)
    if not is_platform_admin(who.user_id if who else None):
        return _refuse(request)
    if request.method != "POST":
        return _space_form(request)

    password = request.POST.get("admin_password") or secrets.token_urlsafe(18)
    try:
        with transaction.atomic():
            space = create_space(
                code=request.POST.get("code", ""),
                title=request.POST.get("title", ""),
                country_code=request.POST.get("country_code", "RS"),
                base_currency=request.POST.get("base_currency", "RSD"),
                report_currency=request.POST.get("report_currency", "EUR"),
                admin_username=request.POST.get("admin_username", ""),
                admin_password=password,
                admin_full_name=request.POST.get("admin_full_name", ""),
                admin_email=platform.email_value(request.POST.get("admin_email", "")),
                role_code=request.POST.get("role_code", "admin"),
            )
            platform.event(who.user_id, _("Пространство создано"), request.POST.get("title", ""),
                           tenant=Tenant.objects.get(pk=space.tenant_id))
    except (SpaceRefused, IntegrityError) as refusal:
        # Отказ — на странице формы и с тем, что человек уже набрал: форма
        # длинная, и причина должна стоять рядом с ней, а не на другом экране.
        return _space_form(request, error=(refusal.message if isinstance(refusal, SpaceRefused)
                                           else _("Код пространства или логин уже занят.")),
                           status=400)

    request.session["platform_notice"] = _(
        "Пространство «%(title)s» заведено. Первый человек может входить."
    ) % {"title": request.POST.get("title", "").strip()}
    if not request.POST.get("admin_password"):
        return _page(request, "created", {
            "space": Tenant.objects.get(pk=space.tenant_id),
            "credentials": {"username": request.POST.get("admin_username", "").strip(),
                            "password": password},
        })
    return redirect("platform-space", tenant_id=space.tenant_id)


def _people(tenant_id):
    """Кто состоит в пространстве и с какими ролями.

    Человек может иметь несколько ролей сразу (T170), поэтому строка — это
    человек со списком, а не членство: иначе один и тот же сотрудник появлялся
    бы в списке дважды и выглядел бы как двое.
    """
    found: dict = {}
    for membership in (
        Membership.objects.filter(tenant_id=tenant_id)
        .select_related("role")
        .order_by("role__title")
    ):
        row = found.setdefault(
            membership.user_id, {"user_id": membership.user_id, "roles": [], "name": ""}
        )
        row["roles"].append({**role_choice(membership.role),
                             "until": membership.expires_at, "membership": membership.pk})

    for user in User.objects.filter(pk__in=found):
        found[user.pk]["name"] = user.full_name or user.username
        found[user.pk]["username"] = user.username
        found[user.pk]["is_active"] = user.is_active
        found[user.pk]["email"] = user.email

    return sorted(found.values(), key=lambda row: row["name"])


def _space_or_none(tenant_id):
    return Tenant.objects.filter(pk=tenant_id).first()


def _no_space(request):
    return render(request, "web/platform/denied.html", {
        "message": _("Такого пространства нет.")
    }, status=404)


@login_required
def space(request, tenant_id):
    """Внутрь пространства: его люди и их роли. Выдача роли — своей страницей."""
    who = get_current_principal(request)
    if not is_platform_admin(who.user_id if who else None):
        return _refuse(request)

    found = _space_or_none(tenant_id)
    if found is None:
        return _no_space(request)

    return render(
        request,
        "web/platform/space.html",
        {
            "space": found,
            "people": _people(tenant_id),
            "organizations_count": LegalEntity.objects.filter(tenant=found).count(),
            "credentials": None,
            "notice": request.session.pop("platform_notice", ""),
            "error": request.session.pop("platform_error", ""),
        },
    )


def _grant_form(request, found, *, error: str = "", status: int = 200):
    chosen = request.POST.get("role_id") or request.GET.get("role") or ""
    user_id = request.POST.get("user_id") or request.GET.get("user") or ""
    member = Membership.objects.filter(
        tenant=found, user_id=_as_uuid(user_id), role_id=_as_uuid(chosen),
    ).first()
    picked_units = request.POST.getlist("unit") if request.method == "POST" else (
        [str(unit) for unit in (member.unit_ids or [])] if member else []
    )
    return render(
        request,
        "web/platform/grant.html",
        {
            "space": found,
            "user_id": user_id,
            "people_options": _people(found.pk),
            "until": request.POST.get("until", "") if request.method == "POST" else (
                member.expires_at.isoformat() if member and member.expires_at else ""
            ),
            "unit_options": list(Unit.objects.filter(tenant_id=found.pk).order_by("code")),
            "picked_units": picked_units,
            "role_options": [{"code": "", "title": _("Выберите роль"),
                              "selected": not chosen}] + [
                {"code": str(role.pk), "title": shown_role_title(role.title, role.code),
                 "selected": str(role.pk) == chosen}
                for role in Role.objects.filter(tenant_id=found.pk).order_by("title")
            ],
            "error": error,
        },
        status=status,
    )


def _as_uuid(raw: str):
    """Идентификатор из формы или `None`, если вписано не то.

    Не идентификатор — тот же ответ, что «такого нет», а не ошибка сервера.
    """
    try:
        return UUID(str(raw).strip())
    except ValueError:
        return None


def _revoke(request, tenant_id, user_id, role):
    try:
        if (user_id is None or role is None or not Membership.objects.filter(
                tenant_id=tenant_id, user_id=user_id).exists()):
            raise SpaceRefused(_("Такого человека в этом пространстве нет."))
        platform.assign(request.user.pk, tenant_id, user_id, role.pk,
                        remove=True, reason=request.POST.get("reason") or _("Снятие роли"))
        request.session["platform_notice"] = _("Роль снята.")
    except SpaceRefused as refusal:
        request.session["platform_error"] = refusal.message
    return redirect("platform-space", tenant_id=tenant_id)


@login_required
def member_role(request, tenant_id):
    """Выдать роль человеку в этом пространстве (форма и отправка) или снять её.

    Снимают роль со страницы людей пространства — кнопкой у самой роли; отказ
    снятия возвращается туда же. Выдача — отдельная страница: у неё своя форма,
    и её отказ стоит рядом с ней.
    """
    who = get_current_principal(request)
    if not is_platform_admin(who.user_id if who else None):
        return _refuse(request)
    found = _space_or_none(tenant_id)
    if found is None:
        return _no_space(request)
    if request.method != "POST":
        return _grant_form(request, found)

    user_id = request.POST.get("user_id") or ""
    role_id = request.POST.get("role_id") or ""
    action = request.POST.get("action") or "grant"

    try:
        role = Role.objects.filter(pk=role_id, tenant_id=tenant_id).first() if role_id else None
    except DjangoValidationError:
        role = None

    if action == "revoke":
        return _revoke(request, tenant_id, _as_uuid(user_id), role)

    if role is None:
        # Роль чужого пространства сюда не приедет: фильтр по тенанту стоит в
        # запросе, а не проверяется после. Подмена идентификатора в форме даёт
        # отказ, а не выдачу роли соседа.
        return _grant_form(request, found,
                           error=_("Такой роли в этом пространстве нет."), status=400)

    try:
        known = User.objects.filter(pk=user_id).exists()
    except DjangoValidationError:
        # Вписано не то, что похоже на идентификатор: это тот же ответ, что и
        # «такого нет», а не ошибка сервера на весь экран.
        known = False
    if not known:
        return _grant_form(request, found, error=_("Такого человека нет."), status=400)

    # Точка — тем же правилом, что на странице партнёра: управляющий без точки
    # получил бы все точки пространства (разбор прав T225).
    unit_ids, refused_unit = membership_units(request.POST.getlist("unit"), tenant_id, role)
    if refused_unit:
        return _grant_form(request, found, error=refused_unit, status=400)

    try:
        platform.assign(who.user_id, tenant_id, _as_uuid(user_id), role.pk,
                        unit_ids=unit_ids, until=platform.expiry(request.POST.get("until")),
                        reason=request.POST.get("reason") or _("Выдача роли"))
    except SpaceRefused as refusal:
        return _grant_form(request, found, error=refusal.message, status=400)
    request.session["platform_notice"] = _("Доступ сохранён.")
    return redirect("platform-space", tenant_id=tenant_id)


def _operator(view):
    from functools import wraps

    @login_required
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not is_platform_admin(request.user.pk):
            return _refuse(request)
        return view(request, *args, **kwargs)
    return guarded


def _page(request, template, context=None, *, error="", status=200):
    response = render(request, "web/platform/" + template + ".html", {
        "credentials": None,
        **(context or {}), "error": error,
        "notice": request.session.pop("platform_notice", ""),
    }, status=status)
    response.headers["Cache-Control"] = "no-store"
    return response


@_operator
def users(request):
    query = request.GET.get("q", "").strip()
    found = User.objects.all().order_by("full_name", "username")
    if query:
        from django.db.models import Q
        found = found.filter(Q(full_name__icontains=query) | Q(email__icontains=query) |
                             Q(username__icontains=query))
    return _page(request, "users", {"users": found[:200], "query": query})


@_operator
def user_detail(request, user_id):
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return _no_space(request)
    error = ""
    if request.method == "POST":
        try:
            reason = request.POST.get("reason", "").strip()
            if not reason:
                raise SpaceRefused(_("Укажите причину изменения."))
            action = request.POST.get("action", "profile")
            if action in ("disable", "enable"):
                platform.set_active(request.user.pk, user.pk, action == "enable", reason)
            else:
                with transaction.atomic():
                    if action == "profile":
                        platform.lock_email(platform.email_value(request.POST.get("email")))
                    user = User.objects.select_for_update().get(pk=user_id)
                    if action == "password":
                        if is_platform_admin(user.pk):
                            raise SpaceRefused(_(
                                "Пароль администратора платформы меняется им самим."))
                        password = secrets.token_urlsafe(18)
                        user.set_password(password)
                        user.save(update_fields=["password"])
                        platform.event(request.user.pk, _("Пароль заменён"), user.username,
                                       reason=reason)
                    elif action == "profile":
                        email = platform.email_value(request.POST.get("email"))
                        if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                            raise SpaceRefused(_(
                                "Эта почта уже принадлежит другой учётной записи."))
                        user.email = email
                        user.full_name = request.POST.get("full_name", "").strip()
                        user.save(update_fields=["email", "full_name"])
                        platform.event(request.user.pk, _("Профиль изменён"), user.username,
                                       reason=reason)
                    else:
                        raise SpaceRefused(_("Неизвестное действие."))
            if action == "password":
                return _page(request, "created", {
                    "credentials": {"username": user.username, "password": password},
                    "person": user,
                })
            request.session["platform_notice"] = _("Изменения сохранены.")
            return redirect("platform-user", user_id=user.pk)
        except SpaceRefused as refusal:
            error = refusal.message
    memberships = Membership.objects.filter(user_id=user.pk).select_related("tenant", "role")
    return _page(request, "user", {"person": user, "memberships": memberships,
                                    "protected_account": is_platform_admin(user.pk)},
                 error=error, status=400 if error else 200)


@_operator
def person_create(request, tenant_id):
    found = _space_or_none(tenant_id)
    if found is None:
        return _no_space(request)
    error = ""
    if request.method == "POST":
        try:
            role = Role.objects.filter(pk=_as_uuid(request.POST.get("role_id")),
                                       tenant=found).first()
            if role is None:
                raise SpaceRefused(_("Выберите роль этого пространства."))
            units, error = membership_units(request.POST.getlist("unit"), tenant_id, role)
            if error:
                raise SpaceRefused(error)
            password = secrets.token_urlsafe(18)
            user, created = platform.add_person(
                request.user.pk, tenant_id, email=request.POST.get("email"),
                full_name=request.POST.get("full_name"), password=password, role_id=role.pk,
                unit_ids=units, until=platform.expiry(request.POST.get("until")),
                reason=request.POST.get("reason"),
            )
            if created:
                return _page(request, "created", {
                    "space": found,
                    "credentials": {"username": user.username, "password": password},
                })
            request.session["platform_notice"] = _("Пользователь добавлен. Доступ действует.")
            return redirect("platform-space", tenant_id=tenant_id)
        except (SpaceRefused, IntegrityError) as refusal:
            error = (refusal.message if isinstance(refusal, SpaceRefused)
                     else _("Учётка уже существует."))
    return _page(request, "person_new", {
        "space": found, "filled": request.POST,
        "roles": Role.objects.filter(tenant=found).order_by("title"),
        "unit_options": Unit.objects.filter(tenant=found).order_by("code"),
        "picked_units": request.POST.getlist("unit"),
    }, error=error, status=400 if error else 200)


@_operator
def organizations(request, tenant_id):
    found = _space_or_none(tenant_id)
    if found is None:
        return _no_space(request)
    if request.method == "POST":
        return organization_edit(request, tenant_id)
    return _page(request, "organizations", {
        "space": found, "organizations": LegalEntity.objects.filter(tenant=found).order_by("title"),
    })


@_operator
def organization_edit(request, tenant_id, organization_id=None):
    found = _space_or_none(tenant_id)
    if found is None:
        return _no_space(request)
    organization = LegalEntity.objects.filter(pk=organization_id, tenant=found).first()
    if organization_id and organization is None:
        return _no_space(request)
    error = ""
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        tax = request.POST.get("tax_number", "").strip()
        if not title:
            error = _("Укажите название организации.")
        else:
            with transaction.atomic():
                if organization is None:
                    organization = LegalEntity.objects.create(tenant=found, title=title,
                                                               tax_number=tax or None)
                    action = _("Организация создана")
                else:
                    organization.title, organization.tax_number = title, tax or None
                    organization.save(update_fields=["title", "tax_number"])
                    action = _("Организация изменена")
                platform.event(request.user.pk, action, title, tenant=found)
            request.session["platform_notice"] = _("Организация сохранена.")
            return redirect("platform-organizations", tenant_id=tenant_id)
    return _page(request, "organization", {
        "space": found, "organization": organization, "filled": request.POST,
    }, error=error, status=400 if error else 200)


@_operator
def history(request):
    events = PlatformEvent.objects.select_related("tenant").order_by("-at")
    tenant_id = _as_uuid(request.GET.get("space", ""))
    if tenant_id:
        events = events.filter(tenant_id=tenant_id)
    entries = list(events[:100])
    names = {str(u.pk): u.full_name or u.username for u in User.objects.filter(
        pk__in=[e.actor_user_id for e in entries],
    )}
    for entry in entries:
        entry.actor_name = names.get(str(entry.actor_user_id), _("Администратор"))
    return _page(request, "history", {"events": entries})


@_operator
def settings(request):
    return _page(request, "settings", {"spaces": Tenant.objects.all().order_by("country_code")})
