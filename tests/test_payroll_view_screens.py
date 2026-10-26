"""Экраны, собранные из сумм расчёта, говорят «нет права», а не показывают ноль.

Найдено ревью безопасности ветки T236. Политика `payroll_view` (`0275`) отдаёт
роли без права ноль строк — и на расчёте, и на проведённом в факты ФОТ. Экран,
который этого не знает, рисует «ФОТ 0», «отклонений нет» или P&L без строки
зарплаты, и всё это читается как посчитанный ответ. Здесь каждый такой экран
проверяется парой: региональный управляющий (права нет) и оперативный
директор (есть), на утверждённом июне сида — с настоящим проведённым ФОТ.
"""
from __future__ import annotations

import re
from datetime import date

from conftest import body, login_as
from test_payrun_retro import as_role, june_approved  # noqa: F401

JUNE_1 = date(2026, 6, 1)
REFUSAL = "не входит в права вашей роли"


def _period(tenant) -> str:
    from core.models import Period

    return str(Period.objects.get(tenant=tenant, period=JUNE_1).id)


def test_pnl_closes_the_payroll_lines_instead_of_dropping_them(
    client, web_env, june_approved,  # noqa: F811
):
    url = f"/periods/{_period(june_approved)}/pnl/"

    login_as(client, "director")
    seen = body(client.get(url))
    assert '<tr data-line="labour_cost">' in seen, "проверять нечего: ФОТ не проведён"

    login_as(client, "regional_manager")
    page = body(client.get(url))
    assert '<tr data-line="labour_cost">' not in page, "ФОТ в P&L виден роли без права"
    assert 'data-line="labour_cost" data-hidden="payroll"' in page
    assert REFUSAL in page and "Итог без зарплаты" in page

    refused = client.get(f"{url}labour_cost/")
    assert refused.status_code == 403
    assert REFUSAL in refused.content.decode()


def test_the_labour_cost_page_refuses_instead_of_showing_zero(
    client, web_env, june_approved,  # noqa: F811
):
    login_as(client, "director")
    assert "Фонд оплаты труда по месяцам" in body(client.get("/labor-cost/"))

    login_as(client, "regional_manager")
    page = body(client.get("/labor-cost/"))
    assert REFUSAL in page
    assert "Фонд оплаты труда по месяцам" not in page

    hours = body(client.get("/work-hours/"))
    assert "Начислено за месяц" not in hours


def test_variance_reconcile_and_trace_refuse_in_words(
    client, web_env, june_approved,  # noqa: F811
):
    from core.models import Payslip

    period = _period(june_approved)
    payslip = Payslip.objects.filter(payrun__tenant=june_approved).first()

    login_as(client, "regional_manager")
    for url in (f"/periods/{period}/variance/", f"/periods/{period}/reconcile/",
                f"/payslips/{payslip.id}/trace/"):
        response = client.get(url)
        assert response.status_code == 403, f"{url}: {response.status_code}"
        assert REFUSAL in response.content.decode(), url

    login_as(client, "director")
    for url in (f"/periods/{period}/variance/", f"/payslips/{payslip.id}/trace/"):
        assert client.get(url).status_code != 403, url


def test_the_readiness_says_the_variance_was_not_checked(web_env, june_approved):  # noqa: F811
    from payrun import readiness

    with as_role("regional_manager"):
        found = readiness._suspicious(june_approved.id, JUNE_1)
    assert [item.title for item in found] == ["Отклонения от прошлого месяца не проверены"]

    with as_role("director"):
        found = readiness._suspicious(june_approved.id, JUNE_1)
    assert not any(re.search("не проверены", item.title) for item in found)
