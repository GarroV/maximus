"""Ввод курсов для консолидации сети: страница администратора платформы (T209).

Справочник курсов общий для всех партнёров и только дописывается, поэтому
тесты пишут курсы в паре валют, которой в продукте нет (`EUR`/`QQQ`, 2001
год): такие строки ничьих сумм не трогают, а удалить их нельзя по построению.
"""
from __future__ import annotations

import pytest

from conftest import body, login_as
from test_platform_admin_screens import platform_admin  # noqa: F401

RATES = "/platform/rates/"


def form(**changes) -> dict:
    data = {"rate_date": "2001-01-31", "base_currency": "eur", "quote_currency": "QQQ",
            "rate": "117,1834", "note": "тест: средний курс"}
    data.update(changes)
    return data


def stored(pair=("EUR", "QQQ")):
    from core.models import FxRate

    return list(FxRate.objects.filter(base_currency=pair[0], quote_currency=pair[1])
                .order_by("entered_at").values_list("rate", "note", "entered_by"))


def test_platform_admin_enters_a_rate_as_published(client, platform_admin):  # noqa: F811
    login_as(client, "admin")
    before = len(stored())

    answer = client.post(RATES, form())

    assert answer.status_code == 302, body(answer)[:300]
    rows = stored()
    assert len(rows) == before + 1
    rate, note, who = rows[-1]
    assert str(rate) == "117.18340000" and note == "тест: средний курс"
    assert who == platform_admin.pk
    assert "1 EUR = 117,1834 QQQ" in body(client.get(RATES))


@pytest.mark.parametrize("changes", [
    {"rate": "0"}, {"rate": "-1"}, {"rate": "abc"}, {"rate": "1.123456789"},
    {"quote_currency": "EUR"}, {"quote_currency": "Q1"}, {"rate_date": "31/01/2001"},
    {"rate_date": "2099-01-31"}, {"note": "н" * 201},
])
def test_a_bad_rate_is_refused_in_words(client, platform_admin, changes):  # noqa: F811
    login_as(client, "admin")
    before = len(stored())

    answer = client.post(RATES, form(**changes))

    assert answer.status_code == 400
    assert '<div class="alert">' in body(answer)
    assert len(stored()) == before


@pytest.mark.parametrize("who", ["director", "accountant"])
def test_a_partner_cannot_enter_rates(client, who):
    login_as(client, who)
    before = len(stored())

    assert client.get(RATES).status_code == 403
    assert client.post(RATES, form()).status_code == 403
    assert len(stored()) == before
