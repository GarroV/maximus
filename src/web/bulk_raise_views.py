"""Массовое изменение ставок: индексация группе одним действием (issue #181).

Зачем. Индексация — обычная ежегодная работа: с первого числа ставки растут у
всей кухни или у всех курьеров. По одному человеку это тридцать открытых
карточек, тридцать заведённых версий и тридцать шансов ошибиться — без единого
способа проверить себя перед тем, как нажать.

**Правка идёт тем же путём, что и по одному.** Каждому заводится своя версия
условий найма с даты (`directory.save_terms`), прошлая закрывается, история
остаётся целой. Массовость здесь — только в том, что действие одно; она не даёт
права записать что-то в обход обычного пути, иначе закрытые месяцы поехали бы.

**Сначала предпросмотр, потом применение.** Отменить массовую правку нечем:
тридцать заведённых версий придётся снимать по одной. Поэтому продукт сперва
показывает поимённо, кого затронет и как изменится ставка, — а список даёт
проверить себя, чего «затронет 12 человек» не даёт.

**Форма — своя страница** (D081, T228): `GET` этого адреса рисует её, `POST` —
предпросмотр или применение. Прежде форма раскрывалась на списке групп; теперь
там ссылка сюда.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from core.models import EmploymentTerm

from . import directory
from .dbrefusal import BadInput, ConstraintRefused, saving
from .directory_views import _date, _guard, _number, _visible_groups
from .format import day, exact

__all__ = ["raise_rates"]


def _new_rate(current: Decimal, percent: Decimal | None, amount: Decimal | None) -> Decimal:
    """Новая ставка: процентом или суммой. Округление — к тому же виду, что была.

    Проценты и сумма не складываются: два способа в одном действии означали бы
    вопрос «что применилось первым», на который человек ответа не увидит.
    """
    if percent is not None:
        return (current * (Decimal(1) + percent / 100)).quantize(current, rounding=ROUND_HALF_UP)
    return current + amount


@login_required
@require_http_methods(["GET", "POST"])
def raise_rates(request):
    who, denied = _guard(request)
    if denied is not None:
        return denied
    if request.method == "GET":
        return _form_page(request, who)

    try:
        group_id = request.POST.get("group") or ""
        # Группа ищется среди видимых роли — тем же отбором, что и в списке
        # формы: группа чужого регистра отвечает «такой нет», как несуществующая
        # (D023).
        group = _visible_groups(who).filter(pk=group_id).first()
        if group is None:
            raise BadInput(_("Такой группы нет."))
        valid_from = _date(request, "valid_from", _("Действует с"), required=True)
        percent = _number(request, "percent", _("Проценты"), required=False)
        amount = _number(request, "amount", _("Сумма"), required=False)
        if percent and amount:
            raise BadInput(
                _("Выберите одно: либо проценты, либо сумму. Вместе они означали бы "
                  "два разных изменения в одном действии.")
            )
        if not percent and not amount:
            raise BadInput(
                _("Ставки не меняются: проценты и сумма пусты или равны нулю.")
            )
    except BadInput as bad:
        return _refusal(request, who, bad.message, bad.http_status)

    # Кого затронет: действующие условия найма людей этой группы. Берём как
    # есть — что видно роли, решают политики базы, а не фильтр здесь.
    terms = list(
        EmploymentTerm.objects.filter(group=group)
        .select_related("employee")
        .order_by("employee__last_name", "employee__first_name")
    )
    rows = [
        {
            "employee": f"{term.employee.last_name} {term.employee.first_name}".strip(),
            "was": exact(term.base_rate),
            "becomes": exact(_new_rate(term.base_rate, percent, amount)),
        }
        for term in terms
    ]

    if request.POST.get("preview"):
        return render(request, "web/directory/raise_preview.html", {
            "heading": _("Кого затронет"),
            "group": group.title,
            # Идентификатор и дата машинным видом — для формы подтверждения:
            # человек читает `since`, а форма отправляет `since_iso`.
            "group_id": str(group.id),
            "since_iso": valid_from.isoformat(),
            "since": day(valid_from),
            "rows": rows,
            "percent": exact(percent) if percent else "",
            "amount": exact(amount) if amount else "",
            "back_url": reverse("directory-groups-raise"),
        })

    changed = 0
    try:
        with saving():
            for term in terms:
                wanted = {
                    name: getattr(term, name) for name in directory.VERSIONED_FIELDS
                }
                wanted["base_rate"] = _new_rate(term.base_rate, percent, amount)
                result = directory.save_terms(
                    who.tenant_id, term.employee_id,
                    valid_from=valid_from, wanted=wanted,
                )
                changed += 1 if result.changed else 0
    except ConstraintRefused as refused:
        return _refusal(request, who, refused.message, refused.http_status)
    except directory.DirectoryRefused as refused:
        return _refusal(request, who, str(refused), 409)

    return redirect(f"{reverse('directory-groups')}?raised={changed}")


def _form_page(request, who, *, error: str = "", status: int = 200):
    """Страница индексации: форма и, если был, отказ — с тем, что уже введено.

    Одна функция на показ и на отказ: две сборки одной формы однажды покажут
    разное. Введённое возвращается в поля — человек набирал дату и проценты
    руками и терять их из-за ошибки в одном поле не должен.
    """
    entered = request.POST if request.method == "POST" else {}
    chosen = str(entered.get("group") or "")
    return render(request, "web/directory/raise_form.html", {
        "heading": _("Поднять ставки группе"),
        "back_url": reverse("directory-groups"),
        "error": error,
        "groups": [
            {"id": group.id, "title": group.title, "selected": str(group.id) == chosen}
            for group in _visible_groups(who).order_by("title")
        ],
        "entered": {
            name: entered.get(name, "") for name in ("valid_from", "percent", "amount")
        },
    }, status=status)


def _refusal(request, who, message: str, status: int):
    """Отказ на странице формы: человек не должен терять введённое."""
    return _form_page(request, who, error=message, status=status)
