"""P&L до утверждения говорит, почему в нём нет зарплаты (issue #308).

Что было. После «Посчитать период» отчёт показывал одну строку «Итог 0,00» без
слова о причине: зарплата попадает в P&L только при утверждении, и человек
решал, что расчёт не дошёл до отчёта. Ложь молчанием — поэтому проверяется и
обратное: после утверждения плашка не должна висеть над настоящими суммами.
"""
from __future__ import annotations

import pytest

from conftest import body, login_as, period_url, wipe_payruns

WAITS = "зарплаты в нём пока нет"


@pytest.fixture
def calculated(client, web_env):
    wipe_payruns(web_env)
    login_as(client, "director")
    url = period_url(client)
    assert client.post(url + "calculate/", follow=True).status_code == 200
    yield url
    client.post("/logout/")


def test_report_before_approval_names_the_missing_payroll(client, calculated):
    html = body(client.get(calculated + "pnl/"))
    assert WAITS in html, "пустой P&L не объясняет, почему в нём нет зарплаты"
    assert "при утверждении периода" in html, "не названо, что делать дальше"


def test_report_after_approval_does_not_claim_payroll_is_missing(client, calculated):
    response = client.post(calculated + "approve/", follow=True)
    assert response.status_code == 200, body(response)[:300]
    html = body(client.get(calculated + "pnl/"))
    assert WAITS not in html, "плашка «зарплаты нет» висит над утверждённым месяцем"
