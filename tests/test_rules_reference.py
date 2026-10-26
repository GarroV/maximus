"""Справочник правил до эталона модуля 17: название, группы, чип у всех режимов (#255–#257).

Проверяется то, что человек видит на экране, а не словарь за ним (словарь — в
`test_rule_names.py`): название рядом с кодом и поиск по нему, группы ссылками со
счётчиком, три режима «Кто меняет» цветными метками, три страницы справочника и
форма новой версии, которая не теряет набранное после отказа.
"""
from __future__ import annotations

import re

import pytest

from conftest import body, login_as

NIGHT = "hour_types.night.pay_percent"
FREE_RULE = "rates.combined_contributions"
LOCKED_RULE = "constants.tax_free_monthly"


@pytest.fixture
def sql(web_env):
    import psycopg

    with psycopg.connect(web_env, autocommit=True) as conn:
        yield conn


@pytest.fixture
def attempts_restored(sql):
    before = [row[0] for row in sql.execute("select id from rule_frame_attempts").fetchall()]
    yield
    if before:
        sql.execute("delete from rule_frame_attempts where id <> all(%s)", (before,))
    else:
        sql.execute("delete from rule_frame_attempts")


def row_of(html: str, path: str) -> str:
    match = re.search(rf"<tr>(?:(?!</tr>).)*?{re.escape(path)}.*?</tr>", html, re.S)
    assert match, f"строки правила {path} на экране нет"
    return match.group(0)


def test_the_list_shows_a_name_next_to_the_code_and_finds_by_it(client):
    """Название стоит рядом с кодом, и поиск находит по нему (#256)."""
    login_as(client, "admin")
    html = body(client.get("/rules/"))
    assert "Ночные — коэффициент оплаты" in row_of(html, NIGHT), "названия в строке нет"
    by_name = body(client.get("/rules/?q=ночные"))
    client.post("/logout/")

    assert NIGHT in by_name, "поиск по названию не нашёл правило"
    assert "rates.net_factor" not in by_name, "поиск по названию ничего не отобрал"


def test_the_summary_and_group_chips_are_links_with_counts(client):
    """Сводка страны и группы-чипы со счётчиками, выбранная — не ссылка (#255, D081/D087)."""
    login_as(client, "admin")
    html = body(client.get("/rules/?group=rates"))
    client.post("/logout/")

    assert "Сербия" in html and "заданы законом" in html, "сводки страны нет"
    assert 'href="/rules/?on=' in html and "group=hour_types" in html, "группы не ссылки"
    assert re.search(r'aria-current="true">Ставки налогов и взносов · 6<', html), (
        "выбранная группа без счётчика или осталась ссылкой"
    )
    assert "rates.net_factor" in html and NIGHT not in html, "группа ничего не отобрала"
    assert "<select" not in html.split("<table")[0], "в роли фильтра появился выпадающий список"


def test_all_three_owner_modes_are_coloured_chips(client):
    """Закон, рамка и «решает партнёр» — три метки; свободное не голый текст (#257)."""
    login_as(client, "admin")
    html = body(client.get("/rules/"))
    client.post("/logout/")

    assert "mark--lock" in row_of(html, LOCKED_RULE)
    assert "mark--frame" in row_of(html, NIGHT)
    free = row_of(html, FREE_RULE)
    assert 'class="mark mark--free"' in free and "Решает партнёр" in free, free[:500]


def test_the_card_has_the_type_the_readers_and_the_effect(client):
    """У карточки есть то, чего не было: тип, что читает, если поменять (#255)."""
    login_as(client, "admin")
    html = body(client.get(f"/rules/{NIGHT}/"))
    free = body(client.get(f"/rules/{FREE_RULE}/"))
    client.post("/logout/")

    assert "<h1>Ночные — коэффициент оплаты</h1>" in html, "название не в заголовке"
    assert f"<code>{NIGHT}</code>" in html, "код пропал с карточки"
    assert re.search(r"<dt>Тип</dt><dd>Число</dd>", html), "тип не в поле карточки"
    assert "Что читает это правило" in html and "Табель" in html and "Расчёт периода" in html
    assert "Если поменять" in html and "Закрытые не двигаются" in html
    assert "mark--free" in free, "у свободного правила на карточке нет цветной метки"


def test_the_two_new_pages_open_by_their_own_addresses(client):
    """«Задано партнёром» и «Как устроено» — страницы со ссылками, а не переключатель."""
    login_as(client, "admin")
    index = body(client.get("/rules/"))
    partner = client.get("/rules/partner/")
    about = client.get("/rules/about/")
    client.post("/logout/")

    assert 'href="/rules/partner/?on=' in index and 'href="/rules/about/?on=' in index
    assert partner.status_code == 200 and "Что изменено поверх страны" in body(partner)
    assert about.status_code == 200 and "Анатомия правила" in body(about)


@pytest.mark.parametrize("address", ["/rules/partner/", "/rules/about/"])
@pytest.mark.parametrize("role", ["director", "accountant", "manager"])
def test_the_new_pages_keep_the_rules_right(client, address, role):
    """Страницы справочника открываются тем же правом, что и сами правила."""
    login_as(client, role)
    answer = client.get(address)
    client.post("/logout/")
    assert answer.status_code == 403, answer.status_code


def test_the_partner_page_lists_an_override_and_a_refused_attempt(
    client, sql, attempts_restored,
):
    login_as(client, "admin")
    refused = client.post(f"/rules/{NIGHT}/new/", {"value": "1.1", "valid_from": "2026-09-01"})
    page = body(client.get("/rules/partner/"))
    client.post("/logout/")

    assert refused.status_code == 400
    assert "Ночные — коэффициент оплаты" in page and "Ставили" in page, page[:900]


def test_the_form_has_no_empty_paragraph_gaps_and_keeps_the_refused_value(
    client, attempts_restored,
):
    """Форма новой версии: поля без пустых абзацев и набранное после отказа (#282)."""
    login_as(client, "admin")
    form = body(client.get(f"/rules/{NIGHT}/new/"))
    refused = body(client.post(
        f"/rules/{NIGHT}/new/", {"value": "1.1", "valid_from": "2026-09-01", "target": "tenant"},
    ))
    client.post("/logout/")

    assert '<p class="field">' not in form, "поле снова обёрнуто в <p>: между полями дыры"
    assert 'name="value"' in refused and 'value="1.1"' in refused, (
        "после отказа в поле не набранное значение"
    )
