"""Что человек читает о правиле, кроме его кода: название, тип, «что читает», «если поменять».

Эталон модуля 17 показывает у каждого правила код **и** человеческое название,
а в панели ещё тип поля, модули-читатели и последствия правки. В теле правил
страны этого нет и быть не должно: тело — числа и значения, подписи интерфейса
ему не принадлежат (та же причина, по которой `rules.SECTION_TITLES` словарь в
коде). Схему базы этот экран не меняет, поэтому всё здесь — словари по коду
правила, а переводы идут обычным каталогом `.po`.

Название собирается из двух половин: **кто** (подпись вида часов, схемы,
надбавки — она уже на языке страницы, пресет сворачивает `title` к нему) и
**что именно** (слова поля, переводит gettext). Так у двухсот правил Сербии
название есть без двухсот отдельных строк перевода, а новый вид часов у другой
страны получает названия своих полей сам.

Чего здесь нет намеренно: названия для правила, которого словарь не знает.
Выдуманное название хуже отсутствующего — `name_of` вернёт пустую строку, и
экран покажет один код.

«Что читает это правило» — не угадано, а привязано к исходникам: для каждого
раздела правил названы файлы, которые его читают, и тест
(`tests/test_rule_names.py`) открывает каждый из них и ищет там ключ раздела.
Переименовали раздел в движке, а карта осталась — тест красный.
"""
from __future__ import annotations

from typing import Any

from django.utils.translation import gettext as _

# Страна по коду. Неизвестный код показывается как есть: новая страна не должна
# ждать правки словаря, чтобы экран вообще открылся.
COUNTRY_NAMES = {
    "RS": lambda: _("Сербия"),
    "ME": lambda: _("Черногория"),
    "HR": lambda: _("Хорватия"),
    "RO": lambda: _("Румыния"),
}


def country_name(code: str) -> str:
    maker = COUNTRY_NAMES.get(code)
    return maker() if maker is not None else code


# --- название ------------------------------------------------------------------

# Правила, у которых нет «субъекта» (вида часов, схемы): путь целиком → слова.
EXACT_NAMES = {
    "constants.min_hourly_rate": lambda: _("Минимальная цена часа, нето"),
    "constants.tax_free_monthly": lambda: _("Необлагаемый минимум в месяц"),
    "constants.min_contribution_base": lambda: _("Минимальная месячная база взносов"),
    "constants.reference_norm_hours": lambda: _("Опорная норма часов в месяц"),
    "constants.full_day_hours": lambda: _("Часов в полном рабочем дне"),
    "rates.income_tax": lambda: _("Ставка налога на доходы"),
    "rates.employee_contributions": lambda: _("Ставка взносов с работника"),
    "rates.employer_contributions": lambda: _("Ставка взносов с работодателя"),
    "rates.net_factor": lambda: _("Коэффициент пересчёта нето в бруто"),
    "rates.combined_contributions": lambda: _("Суммарная ставка взносов"),
    "rates.temporary_contract": lambda: _("Ставка взносов по договору о временных работах"),
    "variance.default.percent": lambda: _("Порог расхождения по умолчанию, %"),
    "variance.default.absolute": lambda: _("Порог расхождения по умолчанию, сумма"),
}

# Разделы, у которых правила лежат под кодом вида (`hour_types.night.<поле>`).
SUBJECT_SECTIONS = ("hour_types", "allowances", "work_measures", "schemes", "groups")
# Разделы из одного узла (`minimum_guarantee.<поле>`): субъект — его собственная подпись.
SINGLE_SECTIONS = ("minimum_guarantee", "manual_correction")

# Что за вещь субъект — только там, где одно и то же имя носят разные вещи:
# схема и группа «Временные работы» — два разных правила с одной подписью.
SUBJECT_NOUNS = {
    "schemes": lambda: _("Схема"),
    "groups": lambda: _("Группа"),
    "work_measures": lambda: _("Мера работы"),
}

# Слова поля. Ключ — остаток пути после субъекта, то есть `gross_up.method` для
# `schemes.standard.gross_up.method`.
FIELD_NAMES = {
    "title": lambda: _("название"),
    "pay_percent": lambda: _("коэффициент оплаты"),
    "counts_as_worked": lambda: _("считается отработанным"),
    "insured": lambda: _("входит в базу взносов"),
    "status": lambda: _("пометка о проверке"),
    "amount_per_norm": lambda: _("сумма за норму"),
    "prorate_by": lambda: _("чем пропорционально"),
    "ledger": lambda: _("регистр учёта"),
    "taxable": lambda: _("облагается налогом"),
    "pay_per_unit": lambda: _("платится за единицу"),
    "monthly": lambda: _("месячная оплата"),
    "proration": lambda: _("пропорция"),
    "hours_per_day": lambda: _("часов в рабочем дне"),
    "worked_hours_source": lambda: _("откуда берутся отработанные часы"),
    "allowances": lambda: _("надбавки"),
    "gross_up.method": lambda: _("способ пересчёта нето в бруто"),
    "gross_up.hours_divisor": lambda: _("делитель часов при пересчёте"),
    "contributions.method": lambda: _("способ расчёта взносов"),
    "contributions.rate_key": lambda: _("какая ставка взносов"),
    "tax.method": lambda: _("способ расчёта налога"),
    "scheme": lambda: _("схема расчёта"),
    "pnl_line": lambda: _("строка P&L"),
    "work_measure": lambda: _("чем меряется работа"),
    "enabled": lambda: _("включена"),
    "base": lambda: _("база"),
    "applies_to": lambda: _("какие часы вызывают доплату"),
}


def _titled(preset: dict, *keys: str) -> str:
    """Подпись узла правил на языке страницы или пусто."""
    node: Any = preset
    for key in keys:
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
    title = node.get("title") if isinstance(node, dict) else None
    return title if isinstance(title, str) else ""


def _field(rest: str) -> str:
    maker = FIELD_NAMES.get(rest)
    return maker() if maker is not None else ""


def _variance_component(preset: dict, code: str) -> str:
    """Подпись компонента порога расхождения: `hours.night` → подпись «Ночные»."""
    kind, _dot, sub = code.partition(".")
    if kind == "hours":
        return _titled(preset, "hour_types", sub) or code
    return _titled(preset, "allowances", code) or _titled(preset, code) or code


def name_of(preset: dict, path: str) -> str:
    """Читаемое название правила на языке страницы. Словарь не знает — пустая строка."""
    exact = EXACT_NAMES.get(path)
    if exact is not None:
        return exact()
    parts = path.split(".")
    section = parts[0]

    if section == "calendar" and len(parts) == 3:
        what = {"norm_hours": _("Норма часов"), "holidays": _("Праздничные дни")}.get(parts[2])
        return f"{what}, {parts[1]}" if what else ""

    if section == "variance" and parts[1:2] == ["components"] and len(parts) >= 4:
        what = {"percent": _("порог, %"), "absolute": _("порог, сумма")}.get(parts[-1])
        if what is None:
            return ""
        return f"{_variance_component(preset, '.'.join(parts[2:-1]))} — {what}"

    if section in SINGLE_SECTIONS and len(parts) >= 2:
        subject = _titled(preset, section)
        field = _field(".".join(parts[1:]))
        return f"{subject} — {field}" if subject and field else ""

    if section in SUBJECT_SECTIONS and len(parts) >= 3:
        subject = _titled(preset, section, parts[1])
        rest = ".".join(parts[2:])
        field = _field(rest)
        if not field and rest.startswith("allowance_prorate."):
            allowance = _titled(preset, "allowances", rest.split(".", 1)[1])
            if allowance:
                field = f"{_('пропорция надбавки')}: {allowance}"
        noun = SUBJECT_NOUNS.get(section)
        if noun is not None:
            subject = f"{noun()}: {subject}"
        return f"{subject} — {field}" if subject and field else ""
    return ""


# --- тип -----------------------------------------------------------------------


def type_of(value: Any, *, has_choices: bool) -> str:
    """Каким полем правится значение — словами. Берётся у самого значения."""
    from . import rules

    if has_choices:
        return _("Выбор из списка")
    return {
        "bool": lambda: _("Переключатель"),
        "number": lambda: _("Число"),
        "list": lambda: _("Список"),
    }.get(rules.kind_of(value), lambda: _("Текст"))()


# --- кто читает ----------------------------------------------------------------

# Раздел правил → (как это называется в продукте, файл, где раздел читается).
# Файл — часть доказательства: тест открывает его и ищет ключ раздела.
READERS = {
    "constants": (("Расчёт ведомости", "payroll/engine.py"),),
    "rates": (("Расчёт ведомости", "payroll/engine.py"),),
    "allowances": (("Расчёт ведомости", "payroll/engine.py"),),
    "minimum_guarantee": (("Расчёт ведомости", "payroll/engine.py"),),
    "manual_correction": (("Расчёт ведомости", "payroll/engine.py"),),
    "schemes": (
        ("Расчёт ведомости", "payroll/engine.py"),
        ("Расчёт периода", "payrun/calc.py"),
    ),
    "hour_types": (
        ("Табель", "timesheets/store.py"),
        ("Табель", "timesheets/grid.py"),
        ("Расчёт периода", "payrun/calc.py"),
        ("Отчёты", "web/reports_views.py"),
    ),
    "work_measures": (
        ("Табель", "timesheets/grid.py"),
        ("Расчёт периода", "payrun/calc.py"),
        ("Расчёт ведомости", "payroll/engine.py"),
    ),
    "groups": (
        ("Табель", "timesheets/grid.py"),
        ("Расчёт периода", "payrun/calc.py"),
    ),
    "variance": (
        ("Готовность периода", "payrun/readiness.py"),
        ("Отчёт расхождений", "reports/variance.py"),
    ),
}

# Названия модулей — строки интерфейса, а не данные: gettext достаёт их из
# литералов, поэтому здесь они перечислены явно, а не берутся из READERS.
READER_LABELS = {
    "Расчёт ведомости": lambda: _("Расчёт ведомости"),
    "Расчёт периода": lambda: _("Расчёт периода"),
    "Табель": lambda: _("Табель"),
    "Отчёты": lambda: _("Отчёты"),
    "Готовность периода": lambda: _("Готовность периода"),
    "Отчёт расхождений": lambda: _("Отчёт расхождений"),
}


def readers_of(path: str) -> list[str]:
    """Модули, читающие правило. Раздела в карте нет — пусто, а не догадка."""
    seen: list[str] = []
    for key, _file in READERS.get(path.split(".")[0], ()):
        if key not in seen:
            seen.append(key)
    return [READER_LABELS[key]() for key in seen]


# --- если поменять -------------------------------------------------------------

# Слова про последствия. Только то, что следует из устройства движка и версий:
# правка с датой не двигает закрытый месяц (D020), а незакрытые месяцы считаются
# по версии, действовавшей в них. Конкретных цифр («затронет 11 человек») здесь
# нет: их пришлось бы считать на данных партнёра, а не писать в словаре.
EFFECT_BY_FIELD = {
    "pay_percent": lambda: _(
        "Меняет начисление за эти часы в незакрытых месяцах. Закрытые не двигаются."
    ),
    "counts_as_worked": lambda: _(
        "Меняет, считаются ли эти часы отработанными, когда месяц сверяется с нормой."
    ),
    "insured": lambda: _("Меняет, входят ли эти часы в базу взносов."),
    "status": lambda: _("Это пометка, а не параметр расчёта: движок её не читает."),
    "title": lambda: _("Меняет только подпись. Сумма расчёта не меняется."),
}

EFFECT_BY_SECTION = {
    "constants": lambda: _(
        "Меняет налог, взносы и доплаты у всех, кого они касаются, в незакрытых месяцах."
    ),
    "rates": lambda: _("Меняет налог и взносы в каждой ведомости незакрытых месяцев."),
    "hour_types": lambda: _("Меняет расчёт часов этого вида в незакрытых месяцах."),
    "allowances": lambda: _("Меняет надбавку у всех, кому она положена, в незакрытых месяцах."),
    "minimum_guarantee": lambda: _("Меняет доплату до минимума в незакрытых месяцах."),
    "manual_correction": lambda: _("Меняет только подпись строки ручной корректировки."),
    "work_measures": lambda: _(
        "Меняет оплату работы, которая меряется этой мерой, в незакрытых месяцах."
    ),
    "schemes": lambda: _("Меняет расчёт у всех сотрудников на этой схеме в незакрытых месяцах."),
    "groups": lambda: _(
        "Меняет, как считаются сотрудники этой группы и куда падают их расходы в P&L."
    ),
    "calendar": lambda: _(
        "Это календарь месяца для первичной загрузки: расчёт по нему сегодня не идёт."
    ),
    "variance": lambda: _(
        "Меняет, при каком расхождении с прошлым месяцем отчёт поднимет замечание. На суммы "
        "расчёта не влияет."
    ),
}


def effect_of(path: str) -> str:
    """Что произойдёт, если поменять правило. Одним предложением-двумя."""
    parts = path.split(".")
    maker = None
    if parts[0] in SUBJECT_SECTIONS:
        maker = EFFECT_BY_FIELD.get(parts[-1])
    return (maker or EFFECT_BY_SECTION.get(parts[0], lambda: ""))()
