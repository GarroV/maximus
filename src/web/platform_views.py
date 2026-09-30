"""Платформенная админка: пространства партнёров, их люди и роли (D065, issue #193).

Отдельная поверхность, а не раздел админки партнёра, и это решение владельца
после двух моих попыток свести их вместе. Довод, который я упускал: дублируются
кнопки, а не инструмент. Обзор над всеми пространствами и статистика по ним в
партнёрской админке отсутствуют **по определению** — она заперта в одном тенанте.

Граница между поверхностями:

| | здесь | админка партнёра (`roles_views`, модуль 11) |
|---|---|---|
| кто | администратор платформы | администратор партнёра |
| что ведёт | сами пространства и людей в любом из них | своих людей, точки, юрлица |
| видит | все пространства | только своё |

**Право — не роль.** Оно лежит в `platform_admins`, куда продукт не пишет вовсе
(`0248`), и спрашивается одной функцией `core.spaces.is_platform_admin`. Роль
партнёра, даже самая полная, платформенной не делает никогда.

**Одна страница — одна функция** (T225, D081, D087): список пространств —
`/platform/`, заведение нового — `/platform/new/`, люди пространства —
`/platform/<id>/`, выдача роли — `/platform/<id>/roles/`. Прежде форма
заведения стояла под списком, а выдача роли — под людьми, и отказ формы
возвращался через сессию на экран списка, далеко от поля, которое надо
поправить. Теперь отказ показывается на странице самой формы, с тем, что уже
набрано (кроме пароля).

**Финансов партнёров здесь нет и не будет.** Миграция `0261` открыла ровно четыре
таблицы — пространства, люди, членства, справочник ролей. Зарплаты, табели и
факты остаются невидимы, и это проверяется тестом на настоящих данных
(`test_platform_admin_access`). Управление доступом — не повод читать чужие
деньги.
"""
from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from core.models import Membership, Role, Tenant, User
from core.roles import DEFAULT_TITLES, ROLE_ORDER
from core.spaces import SpaceRefused, create_space, is_platform_admin

from .principal import get_current_principal


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
        Tenant.objects.annotate(people=Count("membership", distinct=True)).order_by("title")
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
        {"code": code, "title": DEFAULT_TITLES[code], "selected": code == selected}
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
            "spaces": _spaces(),
            "notice": request.session.pop("platform_notice", ""),
            "error": request.session.pop("platform_error", ""),
        },
    )


# Поля формы заведения, которые возвращаются в неё после отказа. Пароля среди
# них нет намеренно: пароль, вписанный сервером обратно в страницу, оседает в
# её коде и в истории браузера.
_SPACE_FIELDS = (
    "title", "code", "country_code", "base_currency", "report_currency",
    "admin_username", "admin_full_name",
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

    try:
        space = create_space(
            code=request.POST.get("code", ""),
            title=request.POST.get("title", ""),
            country_code=request.POST.get("country_code", "RS"),
            base_currency=request.POST.get("base_currency", "RSD"),
            report_currency=request.POST.get("report_currency", "EUR"),
            admin_username=request.POST.get("admin_username", ""),
            admin_password=request.POST.get("admin_password", ""),
            admin_full_name=request.POST.get("admin_full_name", ""),
            role_code=request.POST.get("role_code", "admin"),
        )
    except SpaceRefused as refusal:
        # Отказ — на странице формы и с тем, что человек уже набрал: форма
        # длинная, и причина должна стоять рядом с ней, а не на другом экране.
        return _space_form(request, error=refusal.message, status=400)

    request.session["platform_notice"] = _(
        "Пространство «%(title)s» заведено. Первый человек может входить."
    ) % {"title": request.POST.get("title", "").strip()}
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
        row["roles"].append(membership.role)

    for user in User.objects.filter(pk__in=found):
        found[user.pk]["name"] = user.full_name or user.username
        found[user.pk]["username"] = user.username
        found[user.pk]["is_active"] = user.is_active

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
            "notice": request.session.pop("platform_notice", ""),
            "error": request.session.pop("platform_error", ""),
        },
    )


def _grant_form(request, found, *, error: str = "", status: int = 200):
    chosen = request.POST.get("role_id") or ""
    return render(
        request,
        "web/platform/grant.html",
        {
            "space": found,
            "user_id": (request.POST.get("user_id") or "").strip(),
            "role_options": [
                {"code": str(role.pk), "title": role.title, "selected": str(role.pk) == chosen}
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
    """Снять роль. Отказ — словами на странице людей пространства.

    Человек ищется среди людей ЭТОГО пространства: снятие у чужого или
    несуществующего не должно выглядеть как «роли и не было» — это разные
    ответы на разные вопросы.
    """
    back = redirect("platform-space", tenant_id=tenant_id)
    members = Membership.objects.filter(tenant_id=tenant_id, user_id=user_id) if user_id else None
    if members is None or not members.exists():
        request.session["platform_error"] = _("Такого человека в этом пространстве нет.")
        return back
    if role is None:
        request.session["platform_error"] = _("Такой роли в этом пространстве нет.")
        return back
    removed, _ignored = members.filter(role=role).delete()
    if not removed:
        request.session["platform_error"] = _("Этой роли у человека и не было.")
        return back
    request.session["platform_notice"] = _("Роль снята.")
    return back


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

    _created = Membership.objects.get_or_create(
        tenant_id=tenant_id, user_id=user_id, role=role
    )
    request.session["platform_notice"] = _("Роль выдана.")
    return redirect("platform-space", tenant_id=tenant_id)
