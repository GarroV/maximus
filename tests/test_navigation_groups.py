"""Области учёта подписями левой панели и счётчики ждущей работы (T207, T222).

Эталон собирает навигацию не плоским рядом, а **областями учёта**: «Сбор
данных», «Расчёт», «Отчёты», «Справочники», «Настройки», — а внутри области
лежат её пункты. У пункта стоит число ждущей работы («Инбокс документов · 12»),
и дословное правило эталона: «Счётчик у пункта — это работа, которая ждёт
человека. **Ноль не показываем**».

До 29.09.2026 область была раскрывающимся меню верхней шапки (T207). Владелец
снял это (D081): «никаких выпадающих списков». Теперь область — подпись в левой
панели, а её пункты — список под ней, видный сразу.

Что здесь проверяется и почему именно это.

**Область — не украшение, а способ найти раздел.** Проверяется структура:
пункт лежит **в списке своей области**, а список назван её подписью
(`aria-labelledby`) — иначе диктор прочитал бы пункты без границ.

**Пустая область не рисуется.** Подпись над пустотой обещала бы раздел,
которого у человека нет. В демо там стоит предложение открыть раздел подходящей
ролью (T163) — это закреплено в `tests/test_demo_role_switch.py`.

**Счётчик берётся оттуда же, откуда сам список.** Проверка сравнивает **счётчик
в панели с тем, что показывает сам экран инбокса**, и делает это двумя ролями:
у управляющего база оставляет меньше строк, и число обязано уехать вместе со
списком, а не остаться директорским.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import body, login_as
from test_supplier_invoices import (  # noqa: F401
    NEW,
    counterparty,
    invoice_form,
    invoices_removed,
    key,
    sql,
    tenant,
    units,
)

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "Дизайн-система MAXIMUS" / "Модуль 10 - Вход и каркас.dc.html"


# --- материал -----------------------------------------------------------------


def nav_of(page: str) -> str:
    """Разметка навигации: между `<nav class="sidenav"` и её закрытием."""
    start = page.index('<nav class="sidenav"')
    return page[start:page.index("</nav>", start)]


def areas_of(page: str) -> dict[str, str]:
    """Области учёта: подпись → разметка СПИСКА, который ею назван.

    Именно список, связанный с подписью по `aria-labelledby`, а не «что стоит
    после подписи»: иначе проверка «пункт внутри своей области» проходила бы и
    тогда, когда пункты лежат рядом без всякой связи с ней.
    """
    nav = nav_of(page)
    found = {}
    for ident, title in re.findall(
        r'<span class="sidenav__group" id="([^"]+)">([^<]+)</span>', nav,
    ):
        items = re.search(
            rf'<ul class="sidenav__set" aria-labelledby="{ident}">(.*?)</ul>', nav, re.S,
        )
        assert items, f"у области «{title}» нет списка, названного её подписью"
        found[title.strip()] = items.group(1)
    return found


def counter_in(markup: str) -> int | None:
    """Число ждущей работы или None, если счётчика нет вовсе."""
    found = re.search(r'class="sidenav__count"[^>]*>(?:<span[^>]*>[^<]*</span>)?(\d+)<', markup)
    return int(found.group(1)) if found else None


def waiting_on_the_inbox_screen(client) -> int:
    """Сколько работы ждёт по мнению самого экрана инбокса.

    Считается по его разметке, а не тем же кодом, что собирает счётчик: иначе
    проверка сравнивала бы реализацию сама с собой. Списка на экране два —
    строки без статьи и бумаги с точек, — и оба ждут одного и того же человека.
    """
    page = body(client.get("/inbox/"))
    rows = page.count('data-fact="')
    papers = re.search(r'data-papers="(\d+)"', page)
    return rows + (int(papers.group(1)) if papers else 0)


@pytest.fixture
def unclassified(client, counterparty, units, invoices_removed):  # noqa: F811
    """Два счёта без статьи: один на точке управляющего, один на чужой.

    Разные точки нужны ради проверки среза: у директора в инбоксе оба, у
    управляющего — только его, и счётчик обязан отличаться так же, как список.
    """
    login_as(client, "accountant")
    for unit, number in ((units["NS1"], "EPS-1"), (units["BG1"], "EPS-2")):
        answer = client.post(NEW, invoice_form(
            counterparty, units, item="", unit=unit, number=number, entry_key=key(),
        ))
        assert answer.status_code == 302, body(answer)


# --- области учёта ------------------------------------------------------------


def test_the_areas_are_named_by_the_reference():
    """Название области — слово эталона, а не наше.

    Читается из исходника эталона, а не из списка, переписанного сюда: копия
    разошлась бы с источником истины молча.
    """
    from web import navigation

    text = REFERENCE.read_text(encoding="utf-8")
    block = re.search(r"nav:\s*\{(.*?)\}", text, re.S)
    assert block, "словарь областей в эталоне не найден"
    words = set(re.findall(r'"([А-ЯЁ][^"]{2,40})"', block.group(1)))
    assert "Сбор данных" in words, f"читаем не тот блок эталона: {sorted(words)}"

    strangers = [str(group.title) for group in navigation.GROUPS
                 if str(group.title) not in words]
    assert not strangers, f"области названы не словарём эталона: {strangers}"


def test_the_panel_shows_areas_and_not_a_flat_row(client, web_env):
    """Панель собрана областями, и их ровно столько, сколько ведёт роль."""
    login_as(client, "director")
    page = body(client.get("/periods/"))

    shown = areas_of(page)
    assert "Сбор данных" in shown, f"области учёта не видно: {sorted(shown)}"
    assert "Справочники" in shown, f"области учёта не видно: {sorted(shown)}"


def test_an_item_lives_inside_its_area(client, web_env):
    """Пункт лежит внутри своей области, а не рядом с ней.

    Ради этого задача и делалась: плоский ряд «Табель · Наличные · Инбокс ·
    Сотрудники» не говорит, что первые три — про сбор данных, а четвёртый — про
    справочники.
    """
    login_as(client, "director")
    shown = areas_of(body(client.get("/periods/")))

    collect = shown["Сбор данных"]
    assert "Табель" in collect and "Наличные расходы" in collect
    assert "Инбокс документов" in collect
    assert "Сотрудники" not in collect, "справочник заехал в сбор данных"

    # В области справочников стоит то, что роль ведёт: у директора это
    # «Справочники» целиком (он получил право 28.08.2026, D059), а у роли без
    # права — отдельный пункт «Сотрудники». Проверяется свойство «область не
    # пуста и в ней справочник», а не конкретное имя пункта: имя зависит от
    # прав, и приколоченное сюда снова разъедется с продуктом.
    directories = shown["Справочники"]
    assert "Справочники" in directories or "Сотрудники" in directories, (
        f"в области справочников нет ни одного справочника: {directories[:200]}"
    )


def test_the_current_page_is_marked_inside_its_area(client, web_env):
    """Открытый раздел виден внутри своей области — ничего раскрывать не надо.

    Пометка одна — у самой страницы. Второй такой пометкой (у области) мы бы
    сказали диктору, что открытых страниц две; область и не нуждается в ней:
    она не прячет пункты.
    """
    login_as(client, "director")
    page = body(client.get("/expenses/"))

    assert page.count('aria-current="page"') == 1, (
        "пометка текущей страницы удвоилась: " + str(page.count('aria-current="page"'))
    )
    assert 'aria-current="page"' in areas_of(page)["Сбор данных"]


def test_an_area_a_role_does_not_lead_is_not_drawn_at_all(client, web_env):
    """Область без единого своего пункта в продукте не показывается.

    Открыть её и увидеть пустоту хуже, чем не увидеть кнопку. В демо на этом
    месте стоит предложение открыть раздел подходящей ролью — см.
    `tests/test_demo_role_switch.py`.
    """
    login_as(client, "manager")
    shown = areas_of(body(client.get("/periods/")))

    assert "Настройки" not in shown, "управляющему обещана область, в которой пусто"
    assert "Сбор данных" in shown
    # А то, что он ведёт, на месте: своих людей управляющий читает (D047, T173).
    assert "Сотрудники" in shown.get("Справочники", "")


# --- счётчик ждущей работы ----------------------------------------------------


def test_the_counter_is_the_number_the_inbox_screen_itself_shows(
    client, web_env, unclassified,
):
    """Счётчик равен тому, что показывает сам экран, а не своему числу."""
    login_as(client, "director")
    expected = waiting_on_the_inbox_screen(client)
    assert expected >= 2, "материал не заехал: инбокс пуст"

    shown = areas_of(body(client.get("/periods/")))
    collect = shown["Сбор данных"]
    inbox = collect[collect.index('href="/inbox/"'):]

    assert counter_in(inbox) == expected, (
        f"у пункта {counter_in(inbox)}, а на экране {expected}"
    )


def test_the_counter_is_cut_by_the_database_exactly_like_the_list(
    client, web_env, unclassified,
):
    """У управляющего строк меньше — и число в панели уезжает вместе со списком.

    Это и есть цена второго запроса: он сузился бы иначе и показал бы
    управляющему директорское число.
    """
    login_as(client, "manager")
    mine = waiting_on_the_inbox_screen(client)

    login_as(client, "director")
    all_of_them = waiting_on_the_inbox_screen(client)
    assert mine < all_of_them, (
        f"материал не различает точки: у управляющего {mine}, у директора {all_of_them}"
    )

    login_as(client, "manager")
    shown = areas_of(body(client.get("/periods/")))
    assert counter_in(shown["Сбор данных"]) == mine, (
        f"управляющему показано {counter_in(shown['Сбор данных'])} вместо {mine}"
    )


def test_zero_is_not_shown_at_all(client, web_env, invoices_removed):  # noqa: F811
    """Прямое требование эталона: ноль не рисуется."""
    login_as(client, "director")
    assert waiting_on_the_inbox_screen(client) == 0, "инбокс не пуст, проверка не о том"

    nav = nav_of(body(client.get("/periods/")))
    assert "sidenav__count" not in nav, "пустая очередь показана нулём"
    assert not re.search(r">\s*0\s*<", nav), f"ноль всё-таки нарисован:\n{nav}"
