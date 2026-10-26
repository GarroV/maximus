"""Перенесённая разница — тоже суммы расчёта (T236, `payroll.view`).

Расхождение закрытого месяца (`retro.drift`) считается движком из условий
найма, мимо таблиц, — политика чтения сумм (`0275`) его не видит. Охрана
поэтому стоит в самом `payrun.retro` и проверяется ролью `app_user`, как ходит
продукт. Найдено ревью безопасности на ветке T236.
"""
from __future__ import annotations

from datetime import date

import pytest

from conftest import login_as
from test_payrun_retro import as_role, bump_rates, june_approved  # noqa: F401

JUNE_1 = date(2026, 6, 1)
TWO = ["official", "supplementary"]


def test_the_drift_is_refused_to_a_role_without_sums(web_env, june_approved):  # noqa: F811
    from payrun import retro
    from web import permissions

    bump_rates("2")
    with as_role("regional_manager"), pytest.raises(permissions.PermissionRefused):
        retro.drift(june_approved.id, JUNE_1, visible_ledgers=TWO)
    with as_role("regional_manager"), pytest.raises(permissions.PermissionRefused):
        retro.pending(june_approved.id, JUNE_1)

    # Обратная сторона: та же правка, тот же срез — у управляющего точки, которому
    # ведомость открыта (D031), расхождение есть. Иначе отказ выше ничего не значит.
    with as_role("manager"):
        assert retro.drift(june_approved.id, JUNE_1, visible_ledgers=TWO).lines


def test_the_period_page_shows_no_drift_without_the_right(
    client, web_env, june_approved,  # noqa: F811
):
    """Страница утверждённого месяца: ни строк расхождения, ни итога разницы."""
    bump_rates("2")
    login_as(client, "regional_manager")
    period_id = june_period_id(june_approved)
    page = client.get(f"/periods/{period_id}/")
    assert page.status_code == 200
    # Сводка месяца (T230) несёт расхождение только плашкой со ссылкой.
    assert not page.context["retro_drift"].lines, "расхождение сумм показано роли без права"
    assert not page.context["retro_total"]
    assert not page.context["retro_pending"]
    # Сами строки разницы — на своей странице, и она закрыта целиком.
    assert client.get(f"/periods/{period_id}/retro/").status_code == 404


def june_period_id(tenant) -> str:
    from core.models import Period

    return str(Period.objects.get(tenant=tenant, period=JUNE_1).id)
