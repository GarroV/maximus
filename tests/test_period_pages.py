"""Страница месяца разобрана на отдельные страницы (D081, T230).

Сводка `/periods/<id>/` — ход месяца и действие шага; ведомость, итоги по
регистрам, история и правка задним числом — свои адреса. Проверки идут живым
клиентом и ролями сида, то есть теми же политиками базы, что у человека.

Ядро здесь одно — права: итоги по регистрам не должны нести следа регистра,
которого роль не видит, и страница ведомости обязана оставаться закрытой для
того, кому суммы не положены.
"""
from __future__ import annotations

import pytest

from conftest import body, login_as, period_url, wipe_payruns


@pytest.fixture
def calculated(client, web_env):
    wipe_payruns(web_env)
    login_as(client, "director")
    client.post(period_url(client) + "calculate/", follow=True)


def test_the_month_page_is_a_summary_and_points_to_the_other_pages(client, calculated):
    login_as(client, "director")
    base = period_url(client)
    html = body(client.get(base))
    assert 'class="sheet"' not in html, "ведомость осталась на странице месяца"
    for tail in ("sheet/", "history/"):
        assert f'href="{base}{tail}"' in html, tail
    assert "Прежде чем закрыть" in html or "Утвердить период" in html


def test_the_sheet_lives_on_its_own_page(client, calculated):
    login_as(client, "director")
    html = body(client.get(period_url(client) + "sheet/"))
    assert 'class="sheet"' in html
    assert "Утвердить период" not in html


def test_an_old_cut_link_leads_to_the_sheet_with_the_same_cut(client, calculated):
    login_as(client, "director")
    response = client.get(period_url(client) + "?ledger=official")
    assert response.status_code == 302
    assert response["Location"] == period_url(client) + "sheet/?ledger=official"


def test_the_manager_registers_page_has_no_trace_of_the_internal_ledger(client, calculated):
    """D023: роль без внутреннего регистра не видит его ни строкой, ни в сумме."""
    login_as(client, "director")
    director = body(client.get(period_url(client) + "registers/"))
    login_as(client, "manager")
    manager = body(client.get(period_url(client) + "registers/"))
    assert "Внутренний" in director, "контроль: у директора внутренний регистр есть"
    assert "Внутренний" not in manager
    assert "Официальный" in manager


def test_the_history_page_lists_the_transitions(client, calculated):
    login_as(client, "director")
    html = body(client.get(period_url(client) + "history/"))
    assert 'class="history"' in html


def test_the_retro_page_of_an_open_month_explains_there_is_nothing_to_carry(client, calculated):
    login_as(client, "director")
    html = body(client.get(period_url(client) + "retro/"))
    assert "Месяц ещё не утверждён" in html


def test_every_page_of_the_month_is_closed_to_the_anonymous(client, calculated):
    login_as(client, "director")
    base = period_url(client)
    client.logout()
    for tail in ("", "sheet/", "registers/", "history/", "retro/"):
        assert client.get(base + tail).status_code == 302, tail
