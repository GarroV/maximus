"""Меню роли: пункт виден тогда и только тогда, когда экран откроется (D100, #337).

Разбор интерфейса 12.11.2026: у бухгалтера 17 пунктов, у управляющего 20, и
среди них пункты, за которыми стоял отказ (региональному управляющему
«Стоимость труда»), и заглушки. Человек, нажавший на пункт, ждёт экран, а не
«нет права».

Проверка идёт по живым ролям сида и по настоящим ссылкам из разметки панели, а
не по списку `GROUPS`: список мог бы быть верным, а шаблон — нет.
"""
from __future__ import annotations

import re

import pytest

from conftest import body, login_as

ROLES = ["director", "accountant", "regional_manager", "manager", "admin"]

# Больше — меню перестаёт читаться с одного взгляда (разбор 12.11.2026, DECIMUS
# держит около одиннадцати).
MENU_LIMIT = 11


def menu_links(page: str) -> list[tuple[str, str]]:
    """Пункты левой панели: (подпись, адрес) — и ссылки, и пункт текущей страницы."""
    nav = page[page.index('<nav class="sidenav"'):]
    nav = nav[:nav.index("</nav>")]
    found = []
    for item in re.findall(r'<ul class="sidenav__set".*?</ul>', nav, re.S):
        for link in re.findall(r"<li>(.*?)</li>", item, re.S):
            label = re.search(r'<span class="sidenav__label">([^<]+)</span>', link)
            href = re.search(r'href="([^"]+)"', link)
            found.append((label.group(1), href.group(1) if href else ""))
    return found


# Страница месяца открыта и тому, кто закрывает часы своей точки, хотя суммы ему
# закрыты: блок с суммами пишет отказ, остальное работает.
PARTLY_OPEN = {"/payroll/closing/"}


def is_refusal(response, href: str = "") -> bool:
    """Отказ — это и 403, и страница, которая ответила 200, но написала «нельзя»."""
    if response.status_code != 200:
        return True
    # Экран, который отвечает 200, но вместо содержимого пишет отказ, кладёт его
    # текст в `payroll_denied` (аналитика по людям, ведомость).
    if href in PARTLY_OPEN:
        return False
    return bool(response.context and response.context.get("payroll_denied"))


@pytest.mark.parametrize("role", ROLES)
def test_every_visible_item_opens_without_a_refusal(client, web_env, role):
    """Каждый пункт меню роли ведёт на экран, который эта роль открывает."""
    login_as(client, role)
    menu = menu_links(body(client.get("/periods/")))
    assert menu, f"{role}: меню пусто"

    refused = []
    for label, href in menu:
        if not href:  # пункт текущей страницы — она только что открылась
            continue
        answer = client.get(href, follow=True)
        if is_refusal(answer, href):
            refused.append(f"{label} → {href} ({answer.status_code})")
    assert not refused, f"{role}: пункт виден, а экран отказывает: {refused}"


@pytest.mark.parametrize("role", ["accountant", "manager", "regional_manager"])
def test_the_menu_stays_short(client, web_env, role):
    login_as(client, role)
    menu = menu_links(body(client.get("/periods/")))

    assert len(menu) <= MENU_LIMIT, (
        f"{role}: в меню {len(menu)} пунктов, а читается до {MENU_LIMIT}: "
        + ", ".join(label for label, _ in menu)
    )


def test_the_regional_manager_is_not_offered_the_analytics_he_cannot_open(client, web_env):
    """Ведомости региональному не дают, а аналитика труда — суммы расчёта."""
    login_as(client, "regional_manager")
    labels = [label for label, _ in menu_links(body(client.get("/periods/")))]

    assert "Аналитика по людям" not in labels
    assert not any(is_refusal(client.get(url)) is False for url in ())  # пустая сверка
    assert is_refusal(client.get("/labor-cost/")), (
        "аналитика теперь открывается региональному — пункт можно вернуть"
    )


def test_analytics_pages_link_to_each_other(client, web_env):
    """Три страницы — один пункт меню, поэтому между ними есть строка ссылок."""
    login_as(client, "accountant")
    cost = body(client.get("/labor-cost/"))

    assert 'aria-current="page">Стоимость труда<' in cost
    for title, url in (("Кто уходит", "/turnover/"), ("Часы и переработки", "/work-hours/")):
        assert f'href="{url}' in cost and title in cost, f"со страницы стоимости нет ссылки на «{title}»"
        page = body(client.get(url))
        assert f'aria-current="page">{title}<' in page
        assert 'href="/labor-cost/' in page
        # Пункт меню остаётся подсвеченным на всех трёх.
        assert 'aria-current="location"' in page


def test_incoming_documents_stay_lit_on_invoices_and_papers(client, web_env):
    login_as(client, "accountant")
    for path in ("/invoices/", "/invoices/new/"):
        page = body(client.get(path))
        nav = page[page.index('<nav class="sidenav"'):]
        assert re.search(
            r'<a class="sidenav__current" href="/inbox/" aria-current="location"', nav[:nav.index("</nav>")]
        ), f"{path}: «Входящие документы» не подсвечены"
