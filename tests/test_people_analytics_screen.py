"""Экран аналитики по кадрам: числа сходятся с ведомостью, разрезы не текут (T167).

Критерий приёмки задачи сформулирован числом, а не видом: «числа сходятся с
ведомостями тех же периодов». Поэтому главная проверка здесь — сравнение ДВУХ
СТРАНИЦ: то, что аналитика пишет про июнь, и то, что в июне показывает сама
ведомость. Сравнивать отчёт с пересчётом внутри теста бессмысленно: разойтись он
может только с тем, что человек видит на соседнем экране, — а увидит он именно
ведомость.

**Проверка идёт по разметке, а не по объектам** (тот же довод, что у T116): между
верным объектом и верным экраном помещается форматирование, срез и шаблон, и
ошибка живёт ровно там.

**Прогон ролью `app_user`.** Владелец таблиц обходит RLS; на этом в проекте уже
прожил незамеченным дефект видимости регистров.
"""
from __future__ import annotations

import re

import pytest

from conftest import body, login_as, period_url, wipe_payruns

# Три режима модуля 12 — три страницы (T226, D081). Числа, которые сверяются с
# ведомостью, живут на «Стоимости труда», поэтому она и есть главный экран теста.
SCREEN = "/labor-cost/"
PAGES = {"cost": "/labor-cost/", "churn": "/turnover/", "hours": "/work-hours/"}
OLD_SCREEN = "/analytics/people/"
CURRENT_ITEM = re.compile(
    r'<span class="sidenav__current" aria-current="page"[^>]*>.*?'
    r'<span class="sidenav__label">([^<]+)</span>', re.S,
)

# Итог ведомости стоит последней ячейкой подвала — той, что помечена `num--total`.
SHEET_TOTAL = re.compile(r'class="[^"]*num--total[^"]*"[^>]*>([^<]+)</td>')
BAR = re.compile(r'data-month="([^"]+)" data-payroll="([^"]*)"')
UNIT_ROW = re.compile(r'<tr data-unit="([^"]*)">.*?data-payroll="([^"]*)"', re.S)
GROUP_ROW = re.compile(r'<tr data-group="([^"]*)">.*?data-payroll="([^"]*)"', re.S)


def money_of(text: str) -> float:
    """Сумма с экрана обратно в число: разделители тысяч у продукта свои."""
    return float(re.sub(r"[^\d,.-]", "", text).replace(" ", "").replace(",", "."))


@pytest.fixture(scope="module")
def calculated(web_env):
    """Посчитанный июнь — один раз на модуль.

    Расчёт периода идёт полторы минуты, и повторять его на каждой проверке
    значило бы менять цену прогона в десять раз без единой новой находки.
    Снимается сносом расчёта: цикл периода стережёт база, и обратного перехода
    она не разрешает — как и должна.
    """
    from django.test import Client

    client = Client()
    login_as(client, "director")
    url = period_url(client)
    client.post(url + "calculate/", follow=True)
    yield url

    from core.models import ClosingWaiver, TimesheetClosure

    TimesheetClosure.objects.all().delete()
    ClosingWaiver.objects.all().delete()
    wipe_payruns(web_env)


@pytest.fixture
def screen(client, calculated):
    """Экран аналитики на посчитанном июне, глазами оперативного директора."""
    login_as(client, "director")
    return body(client.get(SCREEN))


def test_экран_открывается_и_называет_себя(screen):
    assert "<h1>Стоимость труда</h1>" in screen


def test_фот_месяца_сходится_с_итогом_ведомости(client, calculated, screen):
    """Главное число экрана — то же, что в подвале ведомости того же месяца.

    Разойтись эти два числа могут только молча: у них разные пути от базы до
    экрана, и второй путь появился именно в этой задаче.
    """
    sheet = body(client.get(calculated))
    totals = SHEET_TOTAL.findall(sheet)
    assert totals, "в подвале ведомости не нашлось итога — сравнивать не с чем"
    payroll = money_of(totals[-1])

    bars = dict(BAR.findall(screen))
    assert bars, "на экране аналитики нет ни одного месяца"
    june = bars["2026-06-01"]
    assert money_of(june) == pytest.approx(payroll, abs=0.01), (
        f"аналитика показывает {june}, ведомость — {totals[-1]}"
    )


def test_разрез_по_точкам_складывается_в_итог(screen):
    """Сумма строк разреза равна итоговой строке: иначе это две правды об одних деньгах."""
    rows = dict(UNIT_ROW.findall(screen))
    total = rows.pop("total", None)
    assert total is not None, "у таблицы точек нет итоговой строки"
    assert rows, "разрез по точкам пуст"
    assert sum(money_of(value) for value in rows.values()) == pytest.approx(
        money_of(total), abs=0.05,
    )


def test_разрез_по_группам_складывается_в_тот_же_итог(screen):
    """Второй разрез тех же денег обязан дать ту же сумму, что и первый."""
    units = dict(UNIT_ROW.findall(screen))
    groups = dict(GROUP_ROW.findall(screen))
    assert groups, "разрез по группам пуст"
    assert sum(money_of(value) for value in groups.values()) == pytest.approx(
        money_of(units["total"]), abs=0.05,
    )


def test_выбор_точки_сужает_числа(client, calculated, screen):
    """Фильтр точки — это фильтр, а не украшение."""
    whole = money_of(dict(UNIT_ROW.findall(screen))["total"])
    codes = [code for code in dict(UNIT_ROW.findall(screen)) if code != "total"]
    assert codes, "не из чего выбрать точку"

    narrowed = body(client.get(f"{SCREEN}?unit={codes[0]}"))
    part = money_of(dict(UNIT_ROW.findall(narrowed))["total"])
    assert 0 < part <= whole, f"точка {codes[0]} даёт {part} против {whole} по всем"


def test_подобранная_чужая_точка_отвечает_как_все(client, calculated, screen):
    """Отказ и пустая таблица одинаково отвечают на вопрос «а есть ли такая точка».

    Правило D023, уже закреплённое на разрезе ведомости (T028): подобранный
    адрес обязан быть неотличим от адреса без параметра вовсе.
    """
    invented = client.get(f"{SCREEN}?unit=НЕТ-ТАКОЙ-ТОЧКИ")
    assert invented.status_code == 200
    mine = dict(UNIT_ROW.findall(body(invented)))
    assert mine["total"] == dict(UNIT_ROW.findall(screen))["total"]


@pytest.mark.parametrize(
    ("page", "expected", "item"),
    [
        ("cost", "Фонд оплаты труда по месяцам", "Стоимость труда"),
        ("churn", "Текучесть", "Кто уходит"),
        ("hours", "Кто вышел за норму", "Часы и переработки"),
    ],
)
def test_три_режима_эталона_три_страницы(client, calculated, page, expected, item):
    """Каждый режим — своя страница, и в левой панели выделен ровно её пункт.

    Выделение считается по корню адреса (`in_section`): окажись у страниц общий
    корень, панель выделила бы все три пункта сразу, и человек не видел бы, где он.
    """
    login_as(client, "director")
    html = body(client.get(PAGES[page]))
    assert expected in html, f"страница {page} не показала того, ради чего она есть"
    assert CURRENT_ITEM.findall(html) == [item], (
        f"на странице {page} выделены пункты {CURRENT_ITEM.findall(html)}"
    )
    assert "<select" not in html, "выбор на странице — выпадающим списком (D081)"


@pytest.mark.parametrize(
    ("query", "target"),
    [
        ("?tab=churn&unit=NS1", "/turnover/?unit=NS1"),
        ("?tab=hours", "/work-hours/"),
        ("", "/labor-cost/"),
        ("?tab=выдумка", "/labor-cost/"),
    ],
)
def test_прежний_адрес_ведёт_на_свою_страницу(client, web_env, query, target):
    """Разосланная ссылка на вкладку открывает ту страницу, которую имел в виду отправитель."""
    login_as(client, "director")
    answer = client.get(OLD_SCREEN + query)
    assert answer.status_code == 302
    assert answer["Location"] == target


def test_доля_фот_от_выручки_прочерк_а_не_ноль(screen):
    """Выручки в продукте нет; ноль в этой строке читался бы как посчитанный ответ."""
    assert "ФОТ к выручке" in screen
    assert "выручка приедет с коннектором Dodo IS" in screen


def test_управляющий_точки_видит_только_свою(client, calculated):
    """Срез делает база, а не фильтр в выборке (D014).

    Управляющий NS1 не должен увидеть на экране ни строки чужой точки — и не
    потому, что мы её спрятали, а потому что политики ему её не отдали.
    """
    login_as(client, "manager")
    html = body(client.get(SCREEN))
    codes = {code for code in dict(UNIT_ROW.findall(html)) if code != "total"}
    assert codes <= {"NS1"}, f"управляющий NS1 увидел точки {codes}"


def test_вкладка_часов_не_молчит_пустотой(client, calculated):
    """Пустая вкладка читается как поломка, а «никто не вышел за норму» — как ответ.

    Какая из двух половин выпадет, зависит от данных месяца, и тест не делает
    вид, что знает это заранее: он требует, чтобы вкладка сказала хоть одну из
    них. Молчание — единственный исход, который здесь считается ошибкой.
    """
    login_as(client, "director")
    html = body(client.get(PAGES["hours"]))
    assert ("Кто вышел за норму" in html) or (
        "никто не вышел за свою норму часов" in html
    ), "вкладка часов не сказала ни про переработки, ни про их отсутствие"
