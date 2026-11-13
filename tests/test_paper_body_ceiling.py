"""Потолок тела запроса при приёме бумаги (issue #148).

Базе не нужна: проверка стоит до разбора формы и читает только заголовок.
"""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from web import papers
from web.dbrefusal import BadInput
from web.papers_views import _refuse_oversized_body, parse_paper


def _post(length: int | None):
    request = RequestFactory().post("/papers/new/", data={})
    if length is None:
        request.META.pop("CONTENT_LENGTH", None)
    else:
        request.META["CONTENT_LENGTH"] = str(length)
    return request


def test_body_far_over_the_ceiling_is_refused_before_it_is_parsed():
    with pytest.raises(BadInput):
        _refuse_oversized_body(_post(papers.MAX_BYTES * 30))


def test_ordinary_photo_body_passes():
    _refuse_oversized_body(_post(papers.MAX_BYTES // 2))
    _refuse_oversized_body(_post(None))


def test_garbage_length_does_not_crash():
    request = _post(0)
    request.META["CONTENT_LENGTH"] = "много"
    _refuse_oversized_body(request)


def test_parse_paper_refuses_oversized_body_without_touching_files():
    request = _post(papers.MAX_BYTES * 30)
    request.FILES  # noqa: B018 — парсинг пустого тела безопасен; важен отказ ниже
    with pytest.raises(BadInput) as refused:
        parse_paper(request, who=None)
    assert "МБ" in refused.value.message
