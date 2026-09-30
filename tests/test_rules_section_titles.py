"""Каждый раздел наших пресетов назван по-человечески.

Механизм «незнакомый раздел показывается своим ключом» стоит в `web.rules`
нарочно: новая страна не должна молча потерять правила с экрана. Но для
**наших** пресетов сырой ключ на экране — это не защита, а забытая подпись:
25.09.2026 сверка с эталоном нашла на экране правил разделы «language» и
«manual_correction» среди десяти переведённых.

Тест смотрит в YAML пресетов, а не в базу: подпись нужна ровно тем разделам,
которые мы поставляем сами.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRESETS = sorted((ROOT / "src/payroll/presets").glob("*.yaml"))


@pytest.mark.parametrize("path", PRESETS, ids=lambda path: path.name)
def test_every_section_of_a_shipped_preset_has_a_title(path):
    from web import rules

    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    nameless = [
        key for key in body
        if key not in rules.NOT_RULES and key not in rules.SECTION_TITLES
    ]
    assert not nameless, (
        f"{path.name}: разделы без подписи — {nameless}. На экране правил они "
        "выйдут сырым ключом рядом с переведёнными: либо дайте подпись в "
        "SECTION_TITLES, либо отнесите к паспорту пресета (IDENTITY)"
    )
