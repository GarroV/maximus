"""Названия, типы, читатели и последствия правил: словарь не отстаёт от пресета (#255, #256).

Предмет проверки — не слова, а полнота и честность. Экран показывает название
рядом с кодом; новое правило в пресете без названия молча вернулось бы к голому
коду, и заметил бы это только тот, кто сравнит экран с файлом. А карта «кто
читает правило» — утверждение про исходники, и пока оно не сверяется с ними,
это просто текст, который устарел на первом же переименовании.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.utils import translation

from payroll.presets import load_preset
from web import rule_names, rules

SRC = Path(__file__).resolve().parents[1] / "src"
LANGUAGES = ["ru", "en", "sr-latn"]


def _leaves(language):
    return rules.leaves(load_preset("serbia-2026", language))


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_serbian_rule_has_a_name_in_every_language(language):
    """У каждого правила набора страны есть название — на каждом из трёх языков."""
    with translation.override(language):
        nameless = [leaf.path for leaf in _leaves(language) if not leaf.title]
    assert not nameless, (
        f"{language}: правила без названия ({len(nameless)}): {nameless[:8]} — "
        "допишите словарь в web/rule_names.py"
    )


def test_two_rules_never_share_a_name():
    """Одно название на два правила — человек выберет не то, не заметив."""
    with translation.override("ru"):
        names = [leaf.title for leaf in _leaves("ru")]
    clashes = sorted({name for name in names if names.count(name) > 1})
    assert not clashes, clashes[:8]


def test_a_rule_the_dictionary_does_not_know_stays_nameless():
    """Неизвестное правило остаётся без названия, а не получает выдуманное."""
    preset = dict(load_preset("serbia-2026"))
    assert rule_names.name_of(preset, "invented.section.field") == ""
    assert rule_names.name_of(preset, "hour_types.no_such_type.pay_percent") == ""


def test_the_name_follows_the_language_of_the_page():
    path = "hour_types.night.pay_percent"
    with translation.override("en"):
        english = rule_names.name_of(dict(load_preset("serbia-2026", "en")), path)
    with translation.override("ru"):
        russian = rule_names.name_of(dict(load_preset("serbia-2026", "ru")), path)
    assert english != russian and "Night" in english and "Ночные" in russian, (english, russian)


def test_every_section_of_the_preset_is_classified_as_read_or_not():
    """Новый раздел пресета обязан попасть в карту читателей или в явный список «не читается»."""
    sections = {path.split(".")[0] for path in (leaf.path for leaf in _leaves("ru"))}
    not_read = {"calendar"}
    unclassified = sections - set(rule_names.READERS) - not_read
    assert not unclassified, f"раздел без карты читателей: {sorted(unclassified)}"
    assert not (set(rule_names.READERS) & not_read), "раздел назван и читаемым, и нечитаемым"


def _file_reads_section(relative: str, section: str) -> bool:
    text = (SRC / relative).read_text(encoding="utf-8")
    return re.search(rf"""["']{re.escape(section)}["']""", text) is not None


@pytest.mark.parametrize(
    ("section", "relative"),
    [(s, f) for s, pairs in sorted(rule_names.READERS.items()) for _label, f in pairs],
)
def test_each_named_reader_really_mentions_its_section(section, relative):
    """Названный модуль и правда читает раздел: иначе карта «что читает» — выдумка."""
    assert _file_reads_section(relative, section), (
        f"{relative} не содержит ключа раздела «{section}» — карта читателей устарела"
    )


def test_the_reader_check_fails_for_a_file_that_does_not_read_the_section():
    """Сама проверка различает: календарь движок не читает, и она это говорит."""
    assert not _file_reads_section("payroll/engine.py", "calendar")
    assert not _file_reads_section("payrun/readiness.py", "hour_types")


def test_a_calendar_rule_says_the_calculation_does_not_read_it():
    assert rule_names.readers_of("calendar.2026-06.norm_hours") == []
    assert "не идёт" in rule_names.effect_of("calendar.2026-06.norm_hours")


def test_every_rule_says_what_happens_if_it_changes():
    silent = [leaf.path for leaf in _leaves("ru") if not rule_names.effect_of(leaf.path)]
    assert not silent, silent[:8]


def test_the_type_comes_from_the_value_and_the_list_of_choices():
    assert rule_names.type_of(True, has_choices=False) == "Переключатель"
    assert rule_names.type_of(1.26, has_choices=False) == "Число"
    assert rule_names.type_of("standard", has_choices=True) == "Выбор из списка"
    assert rule_names.type_of("input", has_choices=False) == "Текст"
    assert rule_names.type_of(["sick"], has_choices=False) == "Список"
