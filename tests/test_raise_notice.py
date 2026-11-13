"""Итог индексации ставок виден на списке групп (issue #284). Базе не нужен."""
from __future__ import annotations

from django.test import RequestFactory

from web.directory_views import _raised_notice


def _notice(query: str) -> str:
    return _raised_notice(RequestFactory().get("/directory/groups/" + query))


def test_count_is_said_in_words():
    assert "7" in _notice("?raised=7")


def test_zero_changes_is_not_dressed_as_success():
    assert "не изменились" in _notice("?raised=0")


def test_no_parameter_and_garbage_say_nothing():
    assert _notice("") == ""
    assert _notice("?raised=-3") == ""
    assert _notice("?raised=много") == ""
