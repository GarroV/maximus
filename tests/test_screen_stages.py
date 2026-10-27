"""Продукт показывает ВСЕ экраны и честно называет состояние каждого (D064).

Владелец 31.08.2026, открыв демо: «в демо нужны вообще все экраны , те что
сейчас в дработке - так и помечаем что В ДОРАБОТКЕ». Причина — не бедность
продукта, а то, что по нему нельзя было пройти: P&L, сверка и выгрузки
отвечали `200`, но в шапку не попали, и владелец решил, что их не построили
(#218). Тот же вывод он сделал пятью днями раньше (`docs/design-gap.md`,
26.08) — то есть один раз это случайность, два раза уже свойство.

Что здесь охраняется и почему именно это.

**Плашек стадии на экранах больше нет** (D098, #309, 27.10.2026): каркас
приведён к DECIMUS, где непостроенный раздел только приглушён в меню.
Состояние модулей живёт в `docs/product-map.md`.

**Пункт, ведущий в никуда, — та самая беда, которой боялись до D064.** Решение
её не отменяет: оно требует, чтобы у ненаписанного экрана была страница со
словами, а не пустота. Значит каждый пункт шапки обязан отвечать.
"""
from __future__ import annotations

import pytest


def test_every_navigation_item_resolves():
    """Ни один пункт шапки не ведёт в никуда — включая ненаписанные экраны."""
    from django.urls import NoReverseMatch, reverse

    from web import navigation

    broken = []
    for group in navigation.GROUPS:
        for item in group.items:
            try:
                reverse(item.route)
            except NoReverseMatch:
                broken.append(f"{item.title} → {item.route}")
    assert not broken, f"пункты шапки без адреса: {broken}"


def test_the_reference_menu_is_covered():
    """Все пункты словаря эталона стоят в шапке — в этом и было решение D064.

    Читается из самого эталона, а не из списка, переписанного сюда: копия
    разошлась бы с источником молча, и тест начал бы охранять её вместо него.
    """
    import re
    from pathlib import Path

    from web import navigation

    root = Path(__file__).resolve().parent.parent
    text = (root / "Дизайн-система MAXIMUS" / "Модуль 10 - Вход и каркас.dc.html").read_text(
        encoding="utf-8",
    )
    menu = re.search(r"menu:\s*\{(.*?)\n\s*\}", text, re.S).group(1)
    wanted = set(re.findall(r'\[\s*"([^"]+)"', menu))

    ours = {str(item.title) for group in navigation.GROUPS for item in group.items}
    # Справочники эталона (Контрагенты, Точки, Юрлица) у нас лежат под одним
    # пунктом «Справочники» — это T173, отдельное решение: два входа в один
    # экран из одной шапки читаются как два разных места. «Доступы» названы
    # «Роли и права» по `navigation.OUR_OWN`.
    under_directories = {"Контрагенты", "Точки", "Юрлица", "Доступы"}
    # «Аналитика по людям» разобрана на три пункта по вкладкам модуля 12 —
    # «Стоимость труда», «Кто уходит», «Часы и переработки» (D081, T226).
    # Исключение держится, только пока все три на месте.
    split = {"Аналитика по людям"} if {
        "Стоимость труда", "Кто уходит", "Часы и переработки",
    } <= ours else set()
    missing = sorted(wanted - ours - under_directories - split)
    assert not missing, f"пункты эталона, которых нет в шапке: {missing}"


# «Аналитика по людям» из этого списка ушла 25.09.2026: экран построен (T167).
# «Банковская выписка» ушла тем же путём (T198). Список набран руками, поэтому
# он и краснеет при таком переходе — это его работа, а не помеха.
@pytest.mark.parametrize("code", ["payouts", "dodo-is"])
def test_a_planned_screen_says_what_it_will_be(client, web_env, code):
    """Ненаписанный экран отвечает и объясняет себя, а не молчит пустотой.

    Без очереди стройки (#309, D098): «Седьмая очередь; ждёт доступа к API»
    человеку за экраном ни о чём не говорит.
    """
    from conftest import body, login_as
    from web import stages

    login_as(client, "director")
    screen = next(item for item in stages.planned_screens() if item.code == code)
    html = body(client.get(f"/{screen.url}"))

    assert str(screen.title) in html, "заглушка не называет сам экран"
    assert str(screen.what)[:40] in html, "заглушка не говорит, что здесь будет"
    assert str(screen.when) not in html, "на экране снова очередь стройки"


def test_no_screen_carries_a_stage_plate(client, web_env):
    """Плашек «Доработка» / «Разработка в процессе» на экранах нет (#309, D098).

    До 27.10.2026 над экраном стояла фраза из журнала стройки («Подсказка
    статьи кнопкой, а не подстановкой в поле»), и бухгалтер читал её как
    поломку. Как в DECIMUS: состояние модулей — в `docs/product-map.md`, а не
    на экране. Ищется класс плашки, а не слово.
    """
    from conftest import body, login_as

    login_as(client, "director")
    for url in ("/inbox/", "/expenses/", "/invoices/", "/rules/", "/payouts/"):
        html = body(client.get(url))
        assert 'class="stage stage--' not in html, f"{url}: плашка стадии снова на экране"
        assert "sidenav__stage" not in html, f"{url}: точка стадии снова у пункта меню"


def test_entry_points_lead_into_the_latest_month(client, web_env):
    """Пункты «Ведомость», «P&L» и сверка ведут в месяц, а не в тупик."""
    from conftest import login_as

    login_as(client, "director")
    for url in ("/payroll/sheet/", "/payroll/closing/", "/reports/pnl/",
                "/reports/reconcile/"):
        response = client.get(url)
        assert response.status_code == 302, f"{url} не переадресует"
        assert response.headers["Location"].startswith("/periods/"), (
            f"{url} ведёт мимо периодов: {response.headers['Location']}"
        )
