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

**Роль — одна плашка, внизу панели, вместе с партнёром, регистрами и точкой.**
Решение владельца — у человека один уровень (расхождение с эталоном — #130).

**Раздел, которого роль не ведёт, не показывается вовсе.** Ссылка на экран,
который ответит отказом, хуже отсутствующей кнопки.

**Клавиатура и диктор.** Первая остановка табуляции — «к содержанию».

**Всё видно и на планшете, ничего не режется** (сверка с эталоном по T222):
панель от 720 до 1099 остаётся полной, подписи переносятся, а не обрезаются
многоточием, у точки состояния есть название, служебное собрано в один пункт.
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
    """В навигации нет раскрывающихся меню и выпадающих списков (D081)."""
    login_as(client, role)
    nav = nav_of(body(client.get("/periods/")))

    assert "<details" not in nav and "<select" not in nav, (
        "раздел снова спрятан за раскрытием — владелец снял это 29.09.2026"
    )
    # Все пункты роли — ссылки прямо в панели: ни один не ждёт раскрытия.
    assert 'href="/expenses/"' in nav and 'href="/inbox/"' in nav


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
    """Карточка платежа лежит под своим корнем адреса, а раздел — инбокс."""
    login_as(client, "accountant")
    nav = sections_of(body(client.get("/payments/new/")))

    assert 'class="sidenav__item" href="/inbox/"' not in nav, (
        "на вложенном экране раздел снова стал ссылкой"
    )
    assert '<span class="sidenav__current" aria-current="page"' in nav


def test_the_section_of_another_root_is_not_highlighted(client, web_env):
    """Выделен один раздел, а не все с похожим адресом."""
    login_as(client, "accountant")
    page = body(client.get("/expenses/"))

    assert page.count('aria-current="page"') == 1, (
        "выделенных разделов не один: " + str(page.count('aria-current="page"'))
    )
    assert 'class="sidenav__item" href="/periods/"' in page


# --- фильтр «человек в этом разделе» -----------------------------------------


@pytest.mark.parametrize(
    ("path", "section", "same"),
    [
        ("/periods/", "/periods/", True),
        # Вложенные экраны остаются в своём разделе: след расчёта и табель
        # открываются из ведомости, карточка платежа и инбокс — из счетов.
        ("/periods/1234/", "/periods/", True),
        ("/payslips/1234/trace/", "/periods/", True),
        ("/timesheets/1234/", "/periods/", True),
        ("/payments/new/", "/invoices/", True),
        ("/inbox/", "/invoices/", True),
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

    Считается по началу атрибута, а не по строке целиком: у плашки теперь есть
    ещё и класс цвета роли (`role role--ops`), и точное совпадение проверяло бы
    оформление вместо количества.
    """
    login_as(client, role)
    page = body(client.get("/periods/"))
    badges = len(re.findall(r'class="role(?: |")', page))

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
    """Язык, тема и выход — низ панели; выход — форма, а не ссылка."""
    login_as(client, "accountant")
    nav = nav_of(body(client.get("/periods/")))
    foot = nav[nav.index('class="sidenav__foot"'):]

    assert 'action="/i18n/setlang/"' in foot, "переключателя языка нет в подвале"
    assert 'action="/theme/"' in foot, "переключателя темы нет в подвале"
    logout = re.search(
        r'<form class="sidenav__form sidenav__logout" method="post" action="/logout/.*?</form>',
        foot, flags=re.S,
    )
    assert logout, "выход не формой: его запустила бы картинка на чужой странице"
    # Выход — значком, поэтому название обязано быть у самой кнопки.
    assert 'aria-label="Выйти"' in logout.group(0), "у кнопки выхода нет названия"
    # Выбранное не нажимается — тот же инвариант, что у текущего раздела.
    assert re.search(r'<span class="sidenav__choice" aria-current="true"[^>]*lang="ru"', foot)


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


def test_a_guest_still_finds_the_guide_in_the_panel(client, web_env):
    """До входа гайд остаётся пунктом: половина его ответа — «кем входить»."""
    nav = nav_of(body(client.get("/guide/")))

    assert 'aria-current="page"' in nav and 'href="/login/"' in nav


def test_the_role_is_named_once_and_in_the_page_language(client, web_env):
    """У учёток сида имя совпадает с названием роли — на любом из языков.

    Сверка по T222: на английской странице стояли жирное «Администратор сети»
    (имя человека, записанное по-русски) и плашка «Network Administrator».
    """
    from django.utils import translation

    login_as(client, "admin")
    client.cookies["django_language"] = "en"
    # Запрос включает английский в потоке теста и там его оставляет; без
    # `override` соседние тесты читали бы названия разделов по-английски.
    with translation.override(None):
        try:
            who = nav_of(body(client.get("/periods/")))
        finally:
            client.cookies.pop("django_language", None)
    who = who[who.index('class="sidenav__user"'):]

    assert "Администратор" not in who, "роль по-русски на английской странице"
    assert "<b>" not in who, "имя повторяет роль: одно и то же сказано дважды"
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
    наоборот) и до `app.css` (продукт уточняет ядро).
    """
    from django.template.loader import render_to_string

    page = render_to_string("web/base.html")
    order = [page.index(f"web/{name}") for name in
             ("tokens.css", "dodo-ds.css", "domain.css", "app.css")]
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
    не видно ни роли, ни партнёра, ни границ между группами, а подпись у значка
    есть только в наведении. Проверяется, что продукт перекрывает каждое
    правило свёртки, которое прятало смысл.
    """
    core = rules(CORE_CSS.read_text(encoding="utf-8"))
    assert ".sidenav__label" in media(core, "@media (max-width: 1099px)"), (
        "ядро больше не сворачивает панель — проверка не о том"
    )
    tablet = media(rules(APP_CSS.read_text(encoding="utf-8")),
                   "@media (min-width: 720px) and (max-width: 1099px)")
    assert "width: 216px" in block(tablet, ".sidenav"), "панель на планшете снова узкая"
    shown = block(tablet, ".sidenav__label, .sidenav__group")
    assert "display: block" in shown, "подписи и группы на планшете спрятаны"
    assert "display: flex" in block(tablet, ".sidenav__name, .sidenav__who"), (
        "на планшете не видно, кто смотрит"
    )


def test_section_names_wrap_instead_of_ending_in_an_ellipsis():
    """«Инбокс докуме…» — не название. Подпись переносится, пункт растёт вниз."""
    css = rules(APP_CSS.read_text(encoding="utf-8"))
    item = block(css, ".sidenav__item, .sidenav__current")
    label = block(css, ".sidenav__label")

    assert "white-space: normal" in item and "height: auto" in item
    assert "min-height: 34px" in item, "пункт стал ниже 34 пикселей"
    assert "text-overflow: clip" in label, "подпись снова режется многоточием"


def test_the_stage_of_a_screen_is_named_on_hover(client, web_env):
    """Цветная точка состояния без слов глазу ничего не говорит."""
    login_as(client, "admin")
    nav = sections_of(body(client.get("/periods/")))

    staged = re.findall(r'<a class="sidenav__item" href="[^"]*" title="([^"]*)">'
                        r'(?:(?!</a>).)*sidenav__stage--', nav, flags=re.S)
    assert staged, "у разделов нет состояния — проверка не о том"
    assert all(" · " in title for title in staged), (
        f"состояние экрана не названо в подсказке: {staged}"
    )


def test_the_phone_scrolls_the_bar_and_not_the_page():
    """Телефон: разделы — полосой у нижнего края, она прокручивается сама.

    Ядро ниже 720 панели не рисует и оставляет это продукту. У управляющего
    разделов больше, чем влезает в 375, — спрятать часть значило бы сделать их
    с телефона недостижимыми, а прокрутить вбок всю страницу — «рассыпалось».
    """
    css = rules(APP_CSS.read_text(encoding="utf-8"))
    phone = media(css, "@media (max-width: 719px)")
    bar = block(phone, ".sidenav__list")

    assert "position: fixed" in bar and "bottom: 0" in bar
    assert "overflow-x: auto" in bar, "полоса разделов не прокручивается сама"
    assert "min-width" not in block(css, "html, body"), (
        "у body появилась фиксированная ширина — страница поедет вбок целиком"
    )
    # Слой продукта из склада ставит body ширину эталона (1280). Если он её
    # ставит, лист продукта обязан её снять — иначе на планшете страница едет
    # вбок целиком (поймано живой проверкой на 900: +380 пикселей).
    domain = rules((STATIC / "domain.css").read_text(encoding="utf-8"))
    if re.search(r"body\s*\{[^}]*min-width", domain):
        own = re.search(r"(?m)^body\s*\{([^}]*)\}", css)
        assert own and "min-width: 0" in own.group(1), (
            "domain.css фиксирует ширину body, а продукт её не снимает"
        )
    assert "padding-bottom" in block(phone, "body"), (
        "низ страницы спрятан под полосой разделов"
    )


def test_the_tap_targets_of_the_unit_manager_are_finger_sized():
    """Сценарии управляющего — телефон 375: всё нажимаемое не меньше 44 пикселей."""
    phone = media(rules(APP_CSS.read_text(encoding="utf-8")), "@media (max-width: 719px)")

    item = block(phone, ".sidenav__list .sidenav__item, .sidenav__list .sidenav__current")
    height = re.search(r"min-height:\s*(\d+)px", item)
    assert height and int(height.group(1)) >= 44, "пункт полосы меньше пальца"
    assert "var(--tap-min)" in block(
        phone,
        ".sidenav__foot .sidenav__item, .sidenav__foot .sidenav__current,\n"
        "  .sidenav__logout .sidenav__item",
    ), "учётная запись и выход на телефоне меньше пальца"
    assert "var(--tap-min)" in block(phone, ".sidenav__theme button, .sidenav__choice"), (
        "язык и тема на телефоне меньше пальца"
    )


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
