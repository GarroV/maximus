"""Левая панель — каркас продукта (T222, D081; до неё — шапка T178).

Что здесь проверяется и почему именно это.

**Разделы видны сразу, без раскрытия.** Владелец 29.09.2026: «никаких
выпадающих списков». Меню, которое надо раскрыть, прячет разделы — человек не
видит, что в системе есть. Поэтому в навигации нет ни одного `<details>`, а
области учёта — подписи над своими пунктами (структура — в
`tests/test_navigation_groups.py`).

**Где человек сейчас.** Раздел, в котором он находится, — не ссылка, а
`span[aria-current="page"]`. Ссылка на открытую страницу обманывает дважды:
мышь обещает переход, которого не будет, а экранный диктор перечисляет её
наравне с остальными и не сообщает, где человек стоит.

**Выделение держится на вложенном экране.** Карточка платежа лежит под своим
корнем адреса (`/payments/…`), но принадлежит разделу инбокса. Проверяется на
настоящей странице: карта `NAV_BELONGS` может быть верной, а панель — собранной
мимо неё.

**Каркас — как в DECIMUS (T241, D098).** До входа нет ни панели, ни разделов;
меню учётки внизу панели открывает партнёра, точку, регистры, тему, язык и
выход; на телефоне — полоса сверху и не больше пяти вкладок снизу.

**Роль — одна плашка, внизу панели, вместе с партнёром, регистрами и точкой.**
Решение владельца — у человека один уровень (расхождение с эталоном — #130).

**Раздел, которого роль не ведёт, не показывается вовсе.** Ссылка на экран,
который ответит отказом, хуже отсутствующей кнопки.

**Клавиатура и диктор.** Первая остановка табуляции — «к содержанию».

**Всё видно и на планшете, ничего не режется** (сверка с эталоном по T222):
панель от 720 до 1099 остаётся полной, подписи переносятся, а не обрезаются
многоточием, служебное собрано в один пункт.
Что все разделы влезают в экран 1440×900 и у каждого пункта есть имя для
диктора, проверяется живым прогоном (`tools/smoke_sidenav.mjs`): это
измеряется в браузере, а не в листе.

Проверки оформления идут по файлам статики. Рядом стоит проверка, что страница
эти файлы просит: правило в неподключённом листе не действует.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import body, login_as

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "web" / "static" / "web"
APP_CSS = STATIC / "app.css"
CORE_CSS = STATIC / "dodo-ds.css"
SHELL_CSS = STATIC / "shell.css"


def rules(css: str) -> str:
    """Лист без комментариев: проверяем правила, а не объяснения к ним."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def block(css: str, selector: str) -> str:
    """Тело одного правила по его селектору."""
    head = css.split(selector + " {", 1)
    assert len(head) == 2, f"правила «{selector}» в листе нет"
    return head[1].split("}", 1)[0]


def media(css: str, query: str) -> str:
    """Тело одного `@media` целиком — до парной закрывающей скобки."""
    start = css.index(query)
    depth, i = 0, css.index("{", start)
    for j in range(i, len(css)):
        depth += {"{": 1, "}": -1}.get(css[j], 0)
        if depth == 0:
            return css[i + 1:j]
    raise AssertionError(f"у {query} нет закрывающей скобки")


def nav_of(page: str) -> str:
    start = page.index('<nav class="sidenav"')
    return page[start:page.index("</nav>", start)]


def sections_of(page: str) -> str:
    """Только список разделов: без марки продукта и без подвала панели.

    Марка ведёт на первый экран месяца, а подвал несёт гайд и пароль, — ссылки,
    которые к разделам отношения не имеют и ловились бы проверками разделов.
    """
    nav = nav_of(page)
    return nav[nav.index('class="sidenav__list"'):nav.index('class="sidenav__foot"')]


# --- разделы видны сразу ------------------------------------------------------


@pytest.mark.parametrize("role", ["director", "manager"])
def test_no_section_hides_behind_a_dropdown(client, web_env, role):
    """Разделы не прячутся за раскрытием (D081).

    Раскрывается только меню учётки — как в DECIMUS (D098): тема, язык и выход
    там же, где кто вошёл. Разделов в нём нет.
    """
    login_as(client, role)
    listed = sections_of(body(client.get("/periods/")))

    assert "<details" not in listed and "<select" not in listed, (
        "раздел снова спрятан за раскрытием — владелец снял это 29.09.2026"
    )
    assert 'href="/expenses/"' in listed and 'href="/inbox/"' in listed


def test_the_old_header_is_gone(client, web_env):
    """Верхней шапки нет: каркас один, второй разъехался бы с первым молча."""
    login_as(client, "accountant")
    page = body(client.get("/periods/"))

    assert 'class="appbar' not in page and 'class="appnav' not in page
    assert page.count('<nav class="sidenav"') == 1


# --- где человек сейчас -------------------------------------------------------


def test_the_current_section_is_not_a_link(client, web_env):
    """Открытый раздел — `span[aria-current]`, и ссылки на него в панели нет."""
    login_as(client, "accountant")
    nav = sections_of(body(client.get("/periods/")))

    assert '<span class="sidenav__current" aria-current="page"' in nav, (
        "текущий раздел не помечен для клавиатуры и диктора"
    )
    assert 'href="/periods/"' not in nav, (
        "панель предлагает уйти на страницу, которая уже открыта"
    )
    # Остальные разделы ссылками остаются — иначе навигации нет вовсе.
    assert '<a class="sidenav__item" href="/expenses/"' in nav


def test_a_nested_screen_keeps_its_section_highlighted(client, web_env):
    """Карточка платежа лежит под своим корнем адреса, а раздел — счета.

    На вложенном экране пункт подсвечен и остаётся ссылкой в начало раздела
    (`aria-current="location"`), как в DECIMUS.
    """
    login_as(client, "accountant")
    nav = sections_of(body(client.get("/payments/new/")))

    assert '<a class="sidenav__current" href="/invoices/" aria-current="location"' in nav, (
        "на вложенном экране раздел не подсвечен"
    )


def test_the_section_of_another_root_is_not_highlighted(client, web_env):
    """Выделен один раздел, а не все с похожим адресом."""
    login_as(client, "accountant")
    nav = sections_of(body(client.get("/expenses/")))

    assert nav.count('aria-current=') == 1, (
        "выделенных разделов не один: " + str(nav.count('aria-current='))
    )
    assert 'class="sidenav__item" href="/periods/"' in nav


# --- фильтр «человек в этом разделе» -----------------------------------------


@pytest.mark.parametrize(
    ("path", "section", "same"),
    [
        ("/periods/", "/periods/", True),
        # Вложенные экраны остаются в своём разделе: след расчёта и табель
        # открываются из ведомости, карточка платежа — из счетов. У инбокса и
        # счетов с 30.09.2026 по пункту в панели (issue #268), и инбокс уже
        # не выделяет счета.
        ("/periods/1234/", "/periods/", True),
        ("/payslips/1234/trace/", "/periods/", True),
        ("/timesheets/1234/", "/periods/", True),
        ("/payments/new/", "/invoices/", True),
        ("/inbox/", "/invoices/", False),
        ("/papers/1234/", "/inbox/", True),
        # Чужой раздел — не выделяется. `/expenses/` и `/invoices/` близкие по
        # смыслу, но это два разных экрана и два разных корня.
        ("/expenses/", "/invoices/", False),
        ("/directory/employees/", "/periods/", False),
        ("/", "/periods/", False),
        # Пустой адрес раздела — не «совпало с корнем», а «сравнивать нечего».
        ("/periods/", "", False),
    ],
)
def test_in_section_compares_roots_not_whole_addresses(path, section, same):
    from web.templatetags.ui import in_section

    assert in_section(path, section) is same, f"{path} против {section}"


# --- кем вошли ---------------------------------------------------------------


@pytest.mark.parametrize("role", ["director", "accountant", "manager", "admin"])
def test_the_header_shows_exactly_one_role(client, web_env, role):
    """Плашка роли одна: решение владельца — у человека один уровень.

    Считается в левой панели: на телефоне то же меню учётки стоит в верхней
    полосе, и в разметке страницы оно повторено (видна одна из двух копий).
    """
    login_as(client, role)
    nav = nav_of(body(client.get("/periods/")))
    badges = len(re.findall(r'class="role(?: |")', nav))

    assert badges == 1, f"{role}: плашек роли {badges}, а должна быть одна"


@pytest.mark.parametrize(
    ("role", "colour"),
    [("director", "role--ops"), ("accountant", "role--accountant"),
     ("manager", "role--manager"), ("admin", "role--admin")],
)
def test_each_role_has_its_own_colour(client, web_env, role, colour):
    """Раздел «Роли» эталона: набор прав узнаётся цветом, а не только словом.

    Цвет берётся из кода роли, а не из её названия: названия переводятся, и на
    английской странице все плашки стали бы одинаковыми.
    """
    login_as(client, role)
    page = body(client.get("/periods/"))

    assert colour in page, f"{role}: плашка без своего цвета"


def test_the_panel_says_whose_eyes_these_numbers_are(client, web_env):
    """Партнёр, регистры и точка — в подвале панели, без наведения мыши.

    Управляющий видит суммы меньше директорских, и объяснение этому обязано
    стоять рядом с числами, а не в справке.
    """
    login_as(client, "manager")
    nav = nav_of(body(client.get("/periods/")))
    who = nav[nav.index('class="sidenav__user"'):]

    assert 'class="sidenav__who"' in who
    assert "Dodo Serbia" in who, "партнёр не назван"
    # Регистры названы метками своего цвета, а не строкой «регистры: все»:
    # у роли на экране названия ровно тех регистров, которые ей видны (D023).
    assert "Официальный" in who, "не сказано, что видно"
    assert "reg--official" in who, "регистр показан, но не своим цветом"
    assert "точка:" in who, "срез по точке не объяснён — суммы меньше без причины"
    assert 'class="role' in who, "роль не в подвале панели"


def test_language_and_logout_live_in_the_foot_of_the_panel(client, web_env):
    """Язык и тема — низ панели; выход — в меню учётки, формой, а не ссылкой."""
    login_as(client, "accountant")
    nav = nav_of(body(client.get("/periods/")))
    foot = nav[nav.index('class="sidenav__foot"'):nav.index('class="sidenav__user"')]
    user = nav[nav.index('class="sidenav__user"'):]

    assert 'action="/i18n/setlang/"' in foot, "переключателя языка нет в подвале"
    assert 'action="/theme/"' in foot, "переключателя темы нет в подвале"
    logout = re.search(
        r'<form class="acct-menu__logout" method="post" action="/logout/">.*?</form>',
        user, flags=re.S,
    )
    assert logout, "выход не формой: его запустила бы картинка на чужой странице"
    assert "Выйти" in logout.group(0), "у кнопки выхода нет названия"
    assert 'role="radiogroup"' in foot and 'class="langpill"' in foot
    assert re.search(
        r'<button type="button" class="langpill__item"[^>]*aria-pressed="true"[^>]*lang="ru"', foot
    )
    assert re.search(
        r'<button type="button" name="theme"[^>]*role="radio" aria-checked="true"', foot
    )


def test_password_and_guide_live_behind_one_service_item(client, web_env):
    """Пароль и гайд — не отдельными пунктами подвала, а на странице учётной записи.

    Сверка по T222: три служебных пункта в подвале выталкивали за нижний край
    экрана 1440×900 целую область учёта. Служебный пункт один, как
    «Настройки» в ядре.
    """
    login_as(client, "accountant")
    nav = nav_of(body(client.get("/periods/")))
    foot = nav[nav.index('class="sidenav__foot"'):]

    assert 'href="/account/"' in foot, "служебного пункта нет"
    for gone in ("/guide/", "/account/password/"):
        assert f'href="{gone}"' not in foot, f"{gone} снова отдельным пунктом подвала"

    page = body(client.get("/account/"))
    assert 'href="/account/password/"' in page and 'href="/guide/"' in page, (
        "со страницы учётной записи не дойти до пароля и гайда"
    )
    assert "Dodo Serbia" in page and 'class="role' in page, "страница не говорит, кто вы"
    assert page.count('aria-current="page"') == 1, "служебный пункт не выделен на своей странице"


def test_the_account_page_is_behind_the_login(client, web_env):
    """Учётная запись без входа — вход, а не пустая страница."""
    response = client.get("/account/")

    assert response.status_code == 302 and "/login/" in response["Location"]


def test_a_guest_finds_the_guide_from_the_login_page(client, web_env):
    """До входа гайд доступен со страницы входа: половина его ответа — «кем входить»."""
    page = body(client.get("/login/"))

    assert 'href="/guide/"' in page, "со страницы входа не дойти до гайда"
    guide = body(client.get("/guide/"))
    assert 'href="/login/"' in guide, "с гайда до входа не дойти"


@pytest.mark.parametrize("url", ["/login/", "/guide/"])
def test_a_guest_sees_no_sections_before_the_login(client, web_env, url):
    """До входа нет ни панели, ни полос телефона (D098, как в DECIMUS).

    Карта разделов — уже сведения о продукте, и каждый раздел гостю всё равно
    ответит формой входа. Владелец 27.10.2026 — на снимке страницы входа, где
    не вошедший видит всё меню: «приведи … окно входа … к единому виду. все как
    и в децимусе».
    """
    page = body(client.get(url))

    assert '<nav class="sidenav"' not in page, "гостю показана левая панель"
    assert 'class="tabbar"' not in page and 'class="mbar"' not in page
    assert "sidenav__group" not in page, "гостю показаны области учёта"
    if url == "/login/":
        # Гайд сам рассказывает о разделах словами и ссылками — это его
        # содержание, а не меню; на форме входа разделов нет вовсе.
        for section in ("/periods/", "/expenses/", "/inbox/", "/payroll/sheet/"):
            assert f'href="{section}"' not in page, f"гостю показан раздел {section}"


def test_the_role_is_named_once_and_in_the_page_language(client, web_env):
    """У учёток сида имя совпадает с названием роли — на любом из языков.

    Сверка по T222: на английской странице стояли жирное «Администратор сети»
    (имя человека, записанное по-русски) и плашка «Network Administrator».
    """
    from django.utils import translation

    login_as(client, "admin")
    client.cookies["django_language"] = "en"
    with translation.override(None):
        try:
            who = nav_of(body(client.get("/periods/")))
        finally:
            client.cookies.pop("django_language", None)
    who = who[who.index('class="sidenav__user"'):]

    assert "Администратор" not in who, "роль по-русски на английской странице"
    summary = who[:who.index("</summary>")]
    assert summary.count("Network Administrator") == 1, (
        "имя повторяет роль: одно и то же сказано дважды"
    )
    assert len(re.findall(r'class="role(?: |")', who)) == 1


@pytest.mark.parametrize(
    ("name", "same"),
    [("Администратор сети", True), ("Network Administrator", True),
     ("Бухгалтер", True), ("", True), ("Ana Petrović", False)],
)
def test_a_name_that_is_a_role_title_in_any_language_is_not_repeated(name, same):
    from web.templatetags.ui import is_role_name

    assert is_role_name(name, "Network Administrator") is same, name


def test_the_full_access_role_is_not_told_it_is_limited_to_a_unit(client, web_env):
    """Точка называется только там, где человек ею ограничен.

    Пусто здесь значит «все точки партнёра»: объяснять надо срезанные данные, а
    не полные.
    """
    login_as(client, "accountant")
    page = body(client.get("/periods/"))

    assert "точка:" not in page, "бухгалтеру приписали ограничение по точке"


# --- разделы по правам -------------------------------------------------------


def test_a_role_does_not_see_sections_it_does_not_lead(client, web_env):
    """Справочники и правила ведёт администратор сети, и только он."""
    login_as(client, "manager")
    page = body(client.get("/periods/"))

    for absent in ("/directory/", "/rules/"):
        assert f'href="{absent}"' not in page, (
            f"управляющему обещан раздел {absent}, который ответит отказом"
        )
    # А то, что видит каждый вошедший, — на месте: права на внесение первичных
    # данных в продукте нет, расход отвергают точка и регистр. Раздел первички
    # ведёт в инбокс, а не в список счетов (issue #162, словарь эталона): работа
    # с бумагами начинается с очереди разбора.
    assert 'href="/expenses/"' in page and 'href="/inbox/"' in page


def test_the_role_that_leads_the_directory_sees_it(client, web_env):
    """Обратная сторона той же проверки: скрыто по праву, а не всегда."""
    login_as(client, "admin")
    page = body(client.get("/periods/"))

    assert 'href="/directory/"' in page and 'href="/rules/"' in page


# --- оформление: обход табом и узкий экран -----------------------------------


def test_the_page_asks_for_the_stylesheets_that_carry_all_this():
    """Правило в неподключённом листе не действует; порядок листов — смысловой.

    Ядро идёт после `tokens.css` (оно перекрывает прежние значения, а не
    наоборот) и до `app.css` (продукт уточняет ядро); каркас `shell.css` —
    последним.
    """
    from django.template.loader import render_to_string

    page = render_to_string("web/base.html")
    order = [page.index(f"web/{name}") for name in
             ("tokens.css", "dodo-ds.css", "domain.css", "app.css", "shell.css")]
    assert order == sorted(order), "листы подключены не в том порядке"


def test_focus_is_visible_on_everything_reachable_by_tab():
    """Фокус — на всём, чем можно управлять, а не только на кнопках и ссылках."""
    css = APP_CSS.read_text(encoding="utf-8")
    assert ":focus-visible { outline:" in css, "видимого фокуса в листе нет"
    assert "button:focus-visible, a:focus-visible" not in css, (
        "фокус снова обещан только кнопкам и ссылкам"
    )


def test_the_first_tab_stop_leads_to_the_content(client, web_env):
    """«К содержанию» — первое, на что встаёт табуляция, и ведёт оно в `main`.

    Панель стоит в разметке раньше экрана: без этой ссылки человек с клавиатуры
    проходил бы два десятка пунктов навигации на каждой странице.
    """
    login_as(client, "accountant")
    page = body(client.get("/periods/"))
    after_body = page[page.index("<body>"):]

    first = re.search(r"<(a|button|input|select|textarea)\b[^>]*>", after_body)
    assert first and 'class="skip-link" href="#content"' in first.group(0), (
        f"первая остановка табуляции не «к содержанию»: {first and first.group(0)}"
    )
    assert '<main id="content"' in page


def test_the_tablet_keeps_the_full_panel():
    """От 720 до 1099 панель остаётся полной, а не значками.

    Ядро сворачивает её здесь до 56 пикселей. Сверка по T222 это сняла: на 900
    не видно ни роли, ни партнёра, ни границ между группами.
    """
    core = rules(CORE_CSS.read_text(encoding="utf-8"))
    assert ".sidenav__label" in media(core, "@media (max-width: 1099px)"), (
        "ядро больше не сворачивает панель — проверка не о том"
    )
    tablet = media(rules(SHELL_CSS.read_text(encoding="utf-8")),
                   "@media (min-width: 720px) and (max-width: 1099px)")
    assert "width: 216px" in block(tablet, ".sidenav"), "панель на планшете снова узкая"
    shown = block(tablet, ".sidenav__label, .sidenav__group")
    assert "display: block" in shown, "подписи и группы на планшете спрятаны"
    assert "display: flex" in block(tablet, ".sidenav__name, .sidenav__who"), (
        "на планшете не видно, кто смотрит"
    )


def test_section_names_wrap_instead_of_ending_in_an_ellipsis():
    """«Инбокс докуме…» — не название. Подпись переносится, пункт растёт вниз."""
    css = rules(SHELL_CSS.read_text(encoding="utf-8"))
    item = block(css, ".sidenav__item, .sidenav__current")
    label = block(css, ".sidenav__label")

    assert "white-space: normal" in item and "height: auto" in item
    assert "min-height: 34px" in item, "пункт стал ниже 34 пикселей"
    assert "text-overflow: clip" in label, "подпись снова режется многоточием"


def test_an_unbuilt_screen_is_quieter_and_says_so(client, web_env):
    """Ненаписанный экран — приглушённый пункт с подсказкой, без точек и плашек.

    Как в DECIMUS (D098, #309): плашки «Доработка» с фразами из журнала стройки
    бухгалтер читал как поломку. Состояние модулей — в `docs/product-map.md`.
    """
    login_as(client, "admin")
    page = body(client.get("/periods/"))
    nav = sections_of(page)

    wip = re.findall(r'<a class="sidenav__item is-wip" href="([^"]*)"[^>]*title="([^"]*)"', nav)
    assert wip, "у ненаписанных разделов нет приглушения — проверка не о том"
    assert all(href.startswith(("/payouts/", "/settings/")) for href, _ in wip), wip
    assert all(" · " in title for _, title in wip), f"не сказано, что раздел строится: {wip}"
    assert "sidenav__stage" not in page and 'class="stage' not in page, (
        "точки или плашки стадии снова на экране"
    )
    assert "sidenav__legend" not in page


def test_the_phone_gets_a_top_bar_and_five_tabs(client, web_env):
    """Телефон, как в DECIMUS: сверху марка и аватар, снизу не больше пяти вкладок.

    До T241 разделы ехали лентой с прокруткой вбок, и подписи на 390 резались
    посередине слова. Сверх пяти — четыре вкладки и «Ещё» со списком.
    """
    css = rules(SHELL_CSS.read_text(encoding="utf-8"))
    phone = media(css, "@media (max-width: 719px)")
    assert "display: none" in block(phone, ".shell > .sidenav")
    bar = block(phone, ".tabbar")
    assert "position: fixed" in bar and "bottom: 0" in bar
    assert "overflow-x" not in bar, "полоса снова прокручивается вбок"
    assert "padding-bottom" in block(phone, "main"), "низ страницы спрятан под полосой"
    app = rules(APP_CSS.read_text(encoding="utf-8"))
    assert "min-width" not in block(app, "html, body"), (
        "у body появилась фиксированная ширина — страница поедет вбок целиком"
    )

    login_as(client, "admin")
    page = body(client.get("/periods/"))
    tabbar = page[page.index('<nav class="tabbar"'):]
    tabbar = tabbar[:tabbar.index("</nav>")]
    shown = re.findall(r'class="tabbar__item', tabbar)
    assert len(shown) <= 5, f"во вкладках {len(shown)} пунктов"
    assert 'class="tabbar__more"' in tabbar, "у администратора разделов больше пяти, а «Ещё» нет"
    assert "is-wip" not in tabbar and "/payouts/" not in tabbar, "ненаписанный экран во вкладках"
    assert 'class="mbar"' in page and 'class="mbar__me"' in page


def test_the_tap_targets_of_the_unit_manager_are_finger_sized():
    """Сценарии управляющего — телефон 375: всё нажимаемое не меньше пальца."""
    phone = media(rules(SHELL_CSS.read_text(encoding="utf-8")), "@media (max-width: 719px)")

    assert "var(--tap-min)" in block(phone, ".tabbar__item"), "вкладка меньше пальца"
    assert "var(--tap-min)" in block(phone, ".tabbar__row"), "строка «Ещё» меньше пальца"
    assert "var(--tap-min)" in block(phone, ".mbar__me"), "аватар меню учётки меньше пальца"


# --- общие правила листа, найденные сверкой блока reports (T222) -------------


def test_the_line_above_a_total_runs_under_every_cell():
    """Первая ячейка итога — обычно `th` («Итого по 12 строкам»).

    Правило только на `td` обрывало линию над итогом ровно перед ней, а у
    итога P&L, где вся строка из `th`, линии не было вовсе.
    """
    css = rules(APP_CSS.read_text(encoding="utf-8"))
    line = block(css, "tfoot td, tfoot th")

    assert "border-top: 2px solid" in line, "у заголовочной ячейки итога нет линии"


def test_the_segment_switch_wraps_instead_of_leaving_the_screen():
    """Переключатель среза на 390 выходил за край: ядро держит его в строку."""
    seg = block(rules(APP_CSS.read_text(encoding="utf-8")), ".seg")

    assert "flex-wrap: wrap" in seg and "max-width: 100%" in seg


# --- экраны внутри месяца: чей пункт подсвечен (#306) ------------------------


@pytest.mark.parametrize(
    ("suffix", "owner"),
    [("sheet/", "/payroll/sheet/"), ("", "/payroll/closing/"),
     ("pnl/", "/reports/pnl/"), ("reconcile/", "/reports/reconcile/")],
)
def test_a_screen_inside_the_month_highlights_its_own_item(client, web_env, suffix, owner):
    """Ведомость, закрытие, P&L и сверка — свои пункты, а не всегда «Табель».

    У всех общий корень `/periods/`, и по корню подсвечивался «Табель»: человек
    нажимал «Ведомость» и видел выделенным другой пункт (#306). Чей экран —
    решает имя маршрута (`web.navigation.OWNER_OF_ROUTE`).
    """
    login_as(client, "accountant")
    month = client.get("/payroll/sheet/")["Location"].split("/")[2]
    nav = sections_of(body(client.get(f"/periods/{month}/{suffix}")))

    current = re.findall(r'class="sidenav__current"[^>]*href="([^"]*)"', nav)
    assert current == [owner], f"/periods/…/{suffix}: подсвечен {current}, а не {owner}"


def test_the_sheet_item_opens_the_sheet_and_not_the_month(client, web_env):
    """«Ведомость» ведёт на ведомость месяца, а не на ту же страницу, что «Закрытие» (#306)."""
    login_as(client, "accountant")

    sheet = client.get("/payroll/sheet/")["Location"]
    closing = client.get("/payroll/closing/")["Location"]
    assert sheet.endswith("/sheet/"), sheet
    assert closing.split("#")[0] != sheet


def test_the_unit_manager_goes_straight_to_the_timesheet(client, web_env):
    """У управляющего точки «Табель» — сразу сетка часов рабочего месяца (#315)."""
    login_as(client, "manager")
    nav = sections_of(body(client.get("/expenses/")))

    assert 'href="/timesheet/"' in nav and 'href="/periods/"' not in nav
    target = client.get("/timesheet/")["Location"]
    assert target.startswith("/timesheets/"), target
    grid = sections_of(body(client.get(target)))
    assert '<a class="sidenav__current" href="/timesheet/" aria-current="location"' in grid


def test_the_director_still_opens_the_list_of_months(client, web_env):
    """Без ограничения точкой «Табель» — список месяцев, как прежде."""
    login_as(client, "director")
    nav = sections_of(body(client.get("/expenses/")))

    assert 'href="/periods/"' in nav and 'href="/timesheet/"' not in nav
