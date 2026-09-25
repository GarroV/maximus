"""Месяц не закрыть с дырами: продукт сам говорит, чего не хватает (#175).

Эталон (модуль 4) формулирует прямо: «список „прежде чем закрыть“ — не отчёт,
который надо открыть, а условие, без которого кнопка не нажимается». Сегодня
утвердить период можно при незакрытых часах, неразобранных бумагах и строках
без статьи — и P&L получится неверным молча.

Три вида находок, и они ведут себя по-разному:

* **блокирующее** — то, из-за чего P&L будет неверным: незакрытые часы точки,
  неразобранные документы, строки без статьи. Утверждение не проходит;
* **предупреждение** — стоит посмотреть, но закрыть не мешает;
* **подозрительное** — то, что может быть правдой: рост вдвое, ноль там, где
  обычно не ноль. Система не знает, ошибка это или жизнь.

Почему проверка не может жить только на экране: экран показывает то, что
человек открыл, а утверждение приходит запросом. Значит отказ обязан стоять на
самом действии, а список — рядом с кнопкой.
"""
from __future__ import annotations

from datetime import date

import pytest
from django.utils import timezone

from conftest import body, login_as, period_url

JUNE = date(2026, 6, 1)


@pytest.fixture
def calculated(client, web_env):
    """Посчитанный, но не утверждённый месяц — состояние перед закрытием.

    Убирается сносом расчёта, а не возвратом состояния: цикл периода стережёт
    база, и «утверждён → посчитан» она не разрешает — как и должна.
    """
    from conftest import wipe_payruns
    from core.models import ClosingWaiver, TimesheetClosure

    login_as(client, "director")
    url = period_url(client)
    client.post(url + "calculate/", follow=True)
    yield url
    TimesheetClosure.objects.all().delete()
    ClosingWaiver.objects.all().delete()
    wipe_payruns(web_env)


def test_the_page_lists_what_is_missing(client, calculated):
    """На странице месяца видно, что мешает закрытию."""
    html = body(client.get(calculated))
    assert "Прежде чем закрыть" in html or "Готовность" in html, (
        "перед утверждением продукт молчит о полноте данных"
    )


def test_open_unit_hours_block_the_approval_where_closing_is_used(client, calculated):
    """Незакрытые часы держат утверждение — но только там, где точки закрывают.

    Само по себе незакрытие часов не делает P&L неверным: числа уже посчитаны
    из внесённых часов. Оно означает, что работа над месяцем не закончена, — и
    это разной силы утверждение в зависимости от того, как партнёр ведёт месяц.

    Партнёр закрыл хоть одну точку — значит механизм у него в ходу, и
    незакрытые остальные держат закрытие. Не закрыл ни одной — требовать этого
    нельзя: месяц оказался бы заперт кнопкой, о которой человек не знает.
    """
    from core.models import Period, Unit
    from timesheets import closing

    first = Unit.objects.order_by("code").first()
    closing.close_unit(
        tenant_id=first.tenant_id, period=JUNE, unit_id=first.id, actor_id=None,
    )

    response = client.post(calculated + "approve/", follow=True)
    assert response.status_code == 409, body(response)[:400]
    assert "час" in body(response).lower(), "отказ не называет причину"
    assert Period.objects.get(period=JUNE).status != "closed"


def test_untouched_closing_does_not_lock_the_month(client, calculated):
    """А если точки никто не закрывал — месяц закрывается, и это не дыра."""
    from core.models import Period

    response = client.post(calculated + "approve/", follow=True)
    assert response.status_code == 200, body(response)[:400]
    assert Period.objects.get(period=JUNE).status == "closed"


def tenant_id():
    """Партнёр сида. Отдельной фикстурой ради этого заводить нечего."""
    from core.models import Tenant

    return Tenant.objects.get(code="rs-dev").id


def test_papers_waiting_for_a_decision_block_it_too(client, calculated):
    """Неразобранная бумага — деньги, которых нет в отчёте.

    Материал тест заводит сам, а не ищет в сиде. Раньше он пропускался, когда
    неразобранных бумаг в сиде не было, — и проверка молча ничего не проверяла:
    прогон зелёный, полнота не проверена. Поймал это шаг «пропуски» в CI, а не
    человек, и правильно: пропуск — это непроверенное, а выглядит как успех.
    """
    from core.models import SourceDocument, Unit

    # Точка обязательна: бумага приходит С ТОЧКИ, и схема этого требует
    # (`source_documents_paper_names_its_unit`). Без неё это не бумага с точки,
    # а документ неизвестно чей.
    handed = SourceDocument.objects.create(
        tenant_id=tenant_id(), kind="invoice", source="manual",
        external_id=f"readiness-paper-{JUNE:%Y%m}", doc_date=JUNE,
        unit=Unit.objects.order_by("code").first(),
        handed_over_at=timezone.now(),
    )
    try:
        # Разобрана — значит у документа появились строки; отдельного признака
        # «разобрано» в продукте нет намеренно (`web/papers`).
        assert not handed.fact_set.exists()

        response = client.post(calculated + "approve/", follow=True)
        assert response.status_code == 409
        assert "бумаг" in body(response).lower() or "документ" in body(response).lower()
    finally:
        handed.delete()


def test_a_clean_month_closes(client, calculated):
    """Всё устранено — месяц закрывается. Иначе проверка была бы тупиком."""
    from core.models import Period, TimesheetClosure, Unit
    from timesheets import closing

    for unit in Unit.objects.all():
        closing.close_unit(
            tenant_id=unit.tenant_id, period=JUNE, unit_id=unit.id, actor_id=None,
        )
    assert TimesheetClosure.objects.exists()

    response = client.post(calculated + "approve/", follow=True)
    assert response.status_code == 200, body(response)[:400]
    assert Period.objects.get(period=JUNE).status == "closed"


def test_a_blocker_can_be_postponed_with_a_reason(client, calculated):
    """Блокирующее можно отложить — с причиной, и она остаётся в протоколе.

    Без этого проверка превращается в тупик: часть находок законна («выписка
    придёт послезавтра, а зарплату платить сегодня»), и запрет без выхода
    заставит закрывать месяц мимо продукта.
    """
    from core.models import Period

    response = client.post(calculated + "postpone/", {
        "finding": "unit_hours", "reason": "точка закроет часы завтра, платим сегодня",
    }, follow=True)
    assert response.status_code == 200, body(response)[:400]

    approved = client.post(calculated + "approve/", follow=True)
    assert approved.status_code == 200, body(approved)[:400]
    assert Period.objects.get(period=JUNE).status == "closed"


def test_postponing_without_a_reason_is_refused(client, calculated):
    """Причина обязательна: отложенное без причины через полгода необъяснимо."""
    response = client.post(calculated + "postpone/", {
        "finding": "unit_hours", "reason": "  ",
    })
    assert response.status_code in (400, 422)


def finding_of(state, code: str):
    """Находка с этим кодом или `None` — включая отложенную."""
    return next((found for found in state.findings if found.code == code), None)


def test_a_salaried_person_short_of_the_norm_is_named_before_closing(
    client, web_env, period_restored,
):
    """Оклад платится полностью, только если в табеле стоят ВСЕ часы месяца.

    Решение владельца (Q025): «базово идет полная выплата по контракту. но
    возможны больничные отпуска и прочее, т.е. друге средства влияния». Делают
    это проценты типов часов: полная норма даёт ровно оклад, отпуск сто
    процентов, больничный шестьдесят пять. Но держится оно на полноте табеля:
    не проставили человеку 40 часов отсутствия — оклад посчитается по 136
    часам, человек получит меньше договора, а расчёт при этом выглядит
    успешным.

    Поэтому проверка на две стороны, и вторая не менее важна первой: те же 136
    часов, дополненные больничным, находки давать не должны — сторож, который
    ругается на законный месяц, перестают читать вместе со всеми остальными.
    """
    from decimal import Decimal

    from core.models import EmploymentTerm
    from core.models import Timesheet as Row
    from payrun import readiness
    from web.format import money

    row = (
        Row.objects.filter(period=JUNE)
        .select_related("employee")
        .order_by("employee__external_id")
        .first()
    )
    assert row is not None, "в сиде нет ни одного табеля за июнь"
    term = (
        EmploymentTerm.objects.filter(employee_id=row.employee_id)
        .order_by("valid_from")
        .last()
    )
    assert term is not None, "у человека с табелем нет условий найма"
    kept = (term.work_measure, term.base_rate, term.coefficient)

    EmploymentTerm.objects.filter(pk=term.pk).update(
        work_measure="salary",
        base_rate=Decimal("90000.00"),
        coefficient=Decimal("1.0"),
    )
    Row.objects.filter(pk=row.pk).update(
        hours={"regular": "136.00"}, norm_hours=Decimal("176.00"),
    )
    try:
        found = finding_of(readiness.check(term.tenant_id, JUNE), "salary_hours")
        assert found is not None, (
            "окладнику не проставили 40 часов месяца, а закрытие месяца молчит"
        )
        said = f"{found.title} {found.detail}"
        assert row.employee.last_name in said, f"не сказано, у кого дыра: {said}"
        assert "40" in said, f"не сказано, скольких часов не хватает: {said}"
        # 90 000 × 40 ч ÷ 176 ч — во столько эти часы обойдутся против контракта.
        assert money(Decimal("20454.55")) in said, (
            f"не сказано, во сколько денег обойдётся разрыв: {said}"
        )
        assert found.kind == readiness.BLOCKING, (
            "находку не отложить с причиной: отложенное считается только у "
            "блокирующего, а пропустить такое молча нельзя"
        )

        Row.objects.filter(pk=row.pk).update(
            hours={"regular": "136.00", "sick": "40.00"},
        )
        assert finding_of(readiness.check(term.tenant_id, JUNE), "salary_hours") is None, (
            "полный табель окладника считается дырой — сторож ругается на "
            "законный месяц"
        )
    finally:
        EmploymentTerm.objects.filter(pk=term.pk).update(
            work_measure=kept[0], base_rate=kept[1], coefficient=kept[2],
        )


def test_the_short_salary_finding_is_visible_on_the_closing_screen(client, calculated):
    """Находка про окладника видна там, где закрывают месяц, а не только в коде.

    Проверка готовности отвечает двоим — экрану и самому утверждению, — и
    разъехавшись, они дают худшее из возможного: страница говорит «всё чисто»,
    а кнопка отвечает отказом. Поэтому находку смотрим глазами страницы.
    """
    from decimal import Decimal

    from core.models import EmploymentTerm
    from core.models import Timesheet as Row
    from web.format import money

    row = (
        Row.objects.filter(period=JUNE)
        .select_related("employee")
        .order_by("employee__external_id")
        .first()
    )
    term = (
        EmploymentTerm.objects.filter(employee_id=row.employee_id)
        .order_by("valid_from")
        .last()
    )
    kept = (term.work_measure, term.base_rate, term.coefficient)
    EmploymentTerm.objects.filter(pk=term.pk).update(
        work_measure="salary",
        base_rate=Decimal("90000.00"),
        coefficient=Decimal("1.0"),
    )
    Row.objects.filter(pk=row.pk).update(
        hours={"regular": "136.00"}, norm_hours=Decimal("176.00"),
    )
    try:
        html = body(client.get(calculated))
        assert row.employee.last_name in html, (
            "экран закрытия месяца не называет окладника с неполным табелем"
        )
        assert money(Decimal("20454.55")) in html, (
            "экран не говорит, во сколько денег обойдётся разрыв"
        )
    finally:
        EmploymentTerm.objects.filter(pk=term.pk).update(
            work_measure=kept[0], base_rate=kept[1], coefficient=kept[2],
        )
