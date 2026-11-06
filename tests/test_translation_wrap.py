"""PO wrapping must preserve exact message keys and idempotent --add (#296)."""

import pytest
from tools.apply_translations import add, existing, unquote, wrap


@pytest.mark.parametrize("text", [
    "  leading and trailing spaces  ",
    " " * 90,
    "word " * 14 + "        indented continuation",
    "Before a newline\n        after a newline with spaces  ",
    'Quoted "text" and a backslash \\ ' * 5,
])
@pytest.mark.parametrize("prefix", ["msgid", "msgstr"])
def test_wrap_roundtrips_all_message_characters(text, prefix):
    wrapped = wrap(prefix, text, width=24).splitlines()
    parts = [wrapped[0][len(prefix) + 1:], *wrapped[1:]]
    assert unquote(parts) == text


def test_add_preserves_multiline_key_and_does_not_duplicate_it(tmp_path):
    po = tmp_path / "django.po"
    po.write_text('msgid ""\nmsgstr ""\n', encoding="utf-8")
    source = "x" * 71 + "   tail\n        Indented next line  "
    table = {source: "Translated " * 10 + "\n        Exact spacing  "}
    assert add(po, table, "fixture.json") == (1, [])
    assert source in existing(po)
    first = po.read_bytes()
    assert add(po, table, "fixture.json") == (0, [])
    assert po.read_bytes() == first
