"""Банковская выписка через экраны: загрузка, сопоставление, решения (T198).

Главные утверждения — о деньгах, и каждое проверяется числом в базе, а не
видом страницы:

1. **повторная загрузка не удваивает ничего** — ни строк, ни платежей, ни
   строк инбокса: тот же файл не ложится вовсе, пересекающийся ложится дублями;
2. **само сводится только однозначное** — внесённый платёж той же суммы, или
   единственный неоплаченный счёт опознанного получателя;
3. **несведённое в учёт не идёт само**, а уходит туда решением человека: оплата
   счёта, строка в инбоксе, «не наш» с причиной;
4. **разбор в инбоксе не возвращает строку в «нужно решить»** — связь держится
   за ключ факта, а не за версию строки.

Файлы — синтетические CSV: настоящих выписок в публичном репозитории нет.
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from conftest import body, login_as
from test_directory import sql  # noqa: F401
from test_supplier_invoices import (  # noqa: F401
    NEW,
    counterparty,
    invoice_form,
    invoices_removed,
    item,
    key,
    tenant,
    units,
)

UPLOAD = "/statement/upload/"
MATCH = "/statement/"
HEADER = "Datum valute;Iznos na teret;Iznos u korist;Naziv primaoca;Svrha plaćanja\n"


def csv_file(*rows: str, name: str = "izvod.csv"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, (HEADER + "".join(rows)).encode("utf-8"), "text/csv")


def upload(client, *rows, name="izvod.csv"):
    return client.post(UPLOAD, {"file": csv_file(*rows, name=name)})


@pytest.fixture
def statements_removed(sql, tenant):  # noqa: F811
    """Выписки теста не переживают его — и строки, заведённые из них."""
    yield
    sql.execute("delete from bank_line_links where tenant_id = %s", (tenant,))
    sql.execute("delete from bank_lines where tenant_id = %s", (tenant,))
    sql.execute("delete from bank_statements where tenant_id = %s", (tenant,))
    sql.execute("delete from facts where dedup_key like 'bank:line:%%'")


@pytest.fixture
def accountant(client, statements_removed, invoices_removed):  # noqa: F811
    login_as(client, "accountant")
    return client


def lines(sql):  # noqa: F811
    return sql.execute(
        """select l.id::text, l.amount, l.duplicate_of is not null, l.not_ours_at is not null,
                  (select string_agg(k.how || ':' || k.fact_key, ',') from bank_line_links k
                    where k.line_id = l.id)
             from bank_lines l
            where l.tenant_id = (select id from tenants where code = 'rs-dev')
            order by l.created_at, l.line_no"""
    ).fetchall()


def facts_like(sql, pattern: str):  # noqa: F811
    return sql.execute(
        "select dedup_key, amount, doc_date, superseded_at is null, source::text "
        "from facts where dedup_key like %s order by created_at",
        (pattern,),
    ).fetchall()


def an_invoice(client, sql, counterparty, units, amount="24000.00", **extra):  # noqa: F811
    answer = client.post(NEW, invoice_form(counterparty, units, amount=amount, **extra))
    assert answer.status_code == 302, body(answer)[:400]
    return sql.execute(
        "select document_id::text from facts where dedup_key like 'manual:invoice:%%' "
        "order by created_at desc limit 1"
    ).fetchone()[0]


# --- повторная загрузка ---------------------------------------------------------


def test_the_same_file_twice_adds_nothing(accountant, sql):  # noqa: F811
    first = upload(accountant, "05.07.2026;1.840,00;;Banca Test;naknada\n")
    assert first.status_code == 302, body(first)[:300]
    again = upload(accountant, "05.07.2026;1.840,00;;Banca Test;naknada\n", name="copy.csv")
    assert again.status_code == 409
    assert "уже загружен" in body(again)
    assert len(lines(sql)) == 1
    assert sql.execute("select count(*) from bank_statements").fetchone()[0] == 1


def test_an_overlapping_file_lays_shared_lines_as_duplicates(accountant, sql):  # noqa: F811
    week = "05.07.2026;1.840,00;;Banca Test;naknada\n"
    upload(accountant, week)
    month = upload(accountant, week, "06.07.2026;5.900,00;;Telekom Test;internet\n",
                   name="month.csv")
    assert month.status_code == 302, body(month)[:300]
    found = lines(sql)
    assert len(found) == 3
    assert [row[2] for row in found] == [False, True, False]   # общая строка — дубль


def test_a_duplicate_never_reaches_the_books(accountant, sql):  # noqa: F811
    """Дубль строки, ушедшей в инбокс, не заводит второй строки инбокса."""
    row = "07.07.2026;3.000,00;;Novi Partner;usluge\n"
    upload(accountant, row)
    line_id = lines(sql)[0][0]
    accountant.post(f"/statement/lines/{line_id}/", {"action": "inbox"})
    upload(accountant, row, "08.07.2026;1,00;;X;y\n", name="later.csv")
    duplicate = lines(sql)[1][0]
    refused = accountant.post(f"/statement/lines/{duplicate}/", {"action": "inbox"})
    assert refused.status_code == 400 and 'data-error="1"' in body(refused)
    assert len(facts_like(sql, "bank:line:%")) == 1


# --- само сводится только однозначное -----------------------------------------------


def test_an_entered_payment_is_matched_by_itself(
    counterparty, item, units, accountant, sql,  # noqa: F811
):
    document = an_invoice(accountant, sql, counterparty, units, item=item)
    paid = accountant.post(f"/invoices/{document}/pay/", {
        "date": "2026-07-10", "amount": "24000.00", "entry_key": key(),
    })
    assert paid.status_code == 302, body(paid)[:300]
    upload(accountant, "11.07.2026;24.000,00;;Someone;struja\n")
    ((_line, _amount, _dup, _not, links),) = lines(sql)
    assert links.startswith("auto:manual:payment:")
    # Ничего нового не заведено: платёж был, выписка его подтвердила.
    assert len(facts_like(sql, "manual:payment:%")) == 1


def test_the_only_unpaid_invoice_of_a_known_payee_is_paid_with_the_bank_date(
    counterparty, item, units, accountant, sql,  # noqa: F811
):
    an_invoice(accountant, sql, counterparty, units, item=item)
    upload(accountant, "12.07.2026;24.000,00;;EPS ELEKTRO;struja 06\n")
    ((line_id, _a, _d, _n, links),) = lines(sql)
    assert links == f"auto:manual:payment:bank-{line_id}"
    ((dedup, amount, on, active, _source),) = facts_like(sql, "manual:payment:%")
    assert (amount, on.isoformat(), active) == (Decimal("24000.00"), "2026-07-12", True)

    # Тот же платёж в пересекающейся выписке второй оплаты не заводит.
    upload(accountant, "12.07.2026;24.000,00;;EPS ELEKTRO;struja 06\n",
           "13.07.2026;1,00;;X;y\n", name="later.csv")
    assert len(facts_like(sql, "manual:payment:%")) == 1


def test_an_unknown_payee_does_not_get_an_invoice_paid(
    counterparty, item, units, accountant, sql,  # noqa: F811
):
    an_invoice(accountant, sql, counterparty, units, item=item)
    upload(accountant, "12.07.2026;24.000,00;;Neko Drugi;struja\n")
    ((_l, _a, _d, _n, links),) = lines(sql)
    assert links is None
    assert facts_like(sql, "manual:payment:%") == []


# --- решения человека -------------------------------------------------------------


def test_a_person_pays_the_invoice_from_the_card(
    counterparty, item, units, accountant, sql,  # noqa: F811
):
    document = an_invoice(accountant, sql, counterparty, units, item=item, amount="30000.00")
    upload(accountant, "12.07.2026;10.000,00;;Neko;avans\n")
    line_id = lines(sql)[0][0]
    card = body(accountant.get(MATCH + f"?line={line_id}"))
    assert f'data-invoice="{document}"' not in card     # чужой и не на ту сумму — не предложен
    done = accountant.post(f"/statement/lines/{line_id}/",
                           {"action": "pay", "invoice": document})
    assert "done=paid" in done["Location"], done["Location"]
    ((_k, amount, on, _active, _s),) = facts_like(sql, "manual:payment:%")
    assert (amount, on.isoformat()) == (Decimal("10000.00"), "2026-07-12")


def test_a_payment_larger_than_the_invoice_is_refused_in_words(
    counterparty, item, units, accountant, sql,  # noqa: F811
):
    document = an_invoice(accountant, sql, counterparty, units, item=item, amount="5000.00")
    upload(accountant, "12.07.2026;10.000,00;;Neko;avans\n")
    line_id = lines(sql)[0][0]
    refused = accountant.post(f"/statement/lines/{line_id}/",
                              {"action": "pay", "invoice": document})
    assert refused.status_code == 400 and 'data-error="1"' in body(refused)
    assert facts_like(sql, "manual:payment:%") == []


def test_sending_to_the_inbox_puts_the_sum_into_pnl_unclassified(accountant, sql):  # noqa: F811
    upload(accountant, "14.07.2026;58.000,00;;Nikola Test PR;usluge po ugovoru\n")
    line_id = lines(sql)[0][0]
    accountant.post(f"/statement/lines/{line_id}/", {"action": "inbox"})
    ((dedup, amount, on, active, source),) = facts_like(sql, "bank:line:%")
    assert (amount, on.isoformat(), source) == (Decimal("58000.00"), "2026-07-14", "bank")
    code = sql.execute(
        "select i.code from facts f join pnl_items i on i.id = f.pnl_item_id "
        "where f.dedup_key = %s and f.superseded_at is null", (dedup,)
    ).fetchone()[0]
    assert code == "unclassified"
    assert 'data-total="58000.00"' in body(accountant.get("/inbox/"))


def test_classifying_in_the_inbox_keeps_the_line_matched(
    item, accountant, sql,  # noqa: F811
):
    """Разбор заменяет версию строки — связь держится за ключ и не рвётся."""
    upload(accountant, "14.07.2026;58.000,00;;Nikola Test PR;usluge\n")
    line_id = lines(sql)[0][0]
    accountant.post(f"/statement/lines/{line_id}/", {"action": "inbox"})
    fact_id = sql.execute(
        "select id from facts where dedup_key like 'bank:line:%%' and superseded_at is null"
    ).fetchone()[0]
    sorted_out = accountant.post(f"/inbox/{fact_id}/classify/",
                                 {"item": item, "unit": "network"})
    assert sorted_out.status_code == 302, body(sorted_out)[:300]
    card = body(accountant.get(MATCH + f"?line={line_id}"))
    assert 'data-state="matched"' in card
    assert 'data-waiting="0"' in card


def test_not_ours_needs_a_reason_and_can_be_taken_back(accountant, sql):  # noqa: F811
    upload(accountant, "15.07.2026;68.400,00;;AIR TEST;avio karta\n")
    line_id = lines(sql)[0][0]
    empty = accountant.post(f"/statement/lines/{line_id}/", {"action": "not_ours", "why": " "})
    assert empty.status_code == 400 and 'data-error="1"' in body(empty)
    accountant.post(f"/statement/lines/{line_id}/",
                    {"action": "not_ours", "why": "личная трата партнёра"})
    assert lines(sql)[0][3] is True
    assert 'data-waiting="0"' in body(accountant.get(MATCH))
    accountant.post(f"/statement/lines/{line_id}/", {"action": "back"})
    assert lines(sql)[0][3] is False


def test_a_manual_link_can_be_undone_but_not_taken_twice(
    counterparty, item, units, accountant, sql,  # noqa: F811
):
    document = an_invoice(accountant, sql, counterparty, units, item=item)
    for when in ("2026-07-10", "2026-07-11"):
        accountant.post(f"/invoices/{document}/pay/",
                        {"date": when, "amount": "12000.00", "entry_key": key()})
    # Два платежа по 12 000 — правило не выбирает между ними.
    upload(accountant, "12.07.2026;12.000,00;;Neko;x\n", "12.07.2026;12.000,00;;Neko;y\n")
    first, second = (row[0] for row in lines(sql))
    assert lines(sql)[0][4] is None
    payment = sql.execute(
        "select dedup_key from facts where dedup_key like 'manual:payment:%%' "
        "order by doc_date limit 1"
    ).fetchone()[0]
    accountant.post(f"/statement/lines/{first}/", {"action": "link", "fact": payment})
    taken = accountant.post(f"/statement/lines/{second}/", {"action": "link", "fact": payment})
    assert taken.status_code == 400 and 'data-error="1"' in body(taken)
    accountant.post(f"/statement/lines/{first}/", {"action": "unlink"})
    assert lines(sql)[0][4] is None


# --- кто видит ----------------------------------------------------------------------


def test_the_unit_manager_is_refused_in_words(client, statements_removed):
    login_as(client, "manager")
    answer = client.get(MATCH)
    assert answer.status_code == 403
    assert "Разбор первички" in body(answer)


def test_the_inbox_names_the_lines_waiting_in_the_statement(accountant, sql):  # noqa: F811
    upload(accountant, "16.07.2026;1.000,00;;Neko;x\n")
    assert 'data-statement="1"' in body(accountant.get("/inbox/"))


def test_an_unreadable_file_is_refused_with_the_reason(accountant):
    from django.core.files.uploadedfile import SimpleUploadedFile

    answer = accountant.post(UPLOAD, {"file": SimpleUploadedFile("x.csv", b"a;b\n1;2\n")})
    assert answer.status_code == 400
    assert "Не нашли колонки" in body(answer)


# --- чужое и подделанное -------------------------------------------------------------


@pytest.fixture
def foreign(sql):  # noqa: F811
    """Строка выписки и счёт другого партнёра — заводит владелец схемы."""
    other = sql.execute(
        """insert into tenants (code, title, country_code, base_currency, report_currency)
           values ('bank-foreign', 'Другой партнёр', 'RS', 'RSD', 'EUR') returning id"""
    ).fetchone()[0]
    statement = sql.execute(
        "insert into bank_statements (tenant_id, format, content_hash) "
        "values (%s, 'table', 'foreign') returning id", (other,),
    ).fetchone()[0]
    line = sql.execute(
        """insert into bank_lines (tenant_id, statement_id, line_no, op_date, direction,
                                   amount, dedup_key)
           values (%s, %s, 1, '2026-07-12', 'out', 1000.00, 'foreign-1') returning id""",
        (other, statement),
    ).fetchone()[0]
    document = sql.execute(
        """insert into source_documents (tenant_id, kind, source, external_id, doc_date)
           values (%s, 'invoice', 'manual', 'foreign-doc', '2026-07-01') returning id""",
        (other,),
    ).fetchone()[0]
    # Бухгалтер состоит и у этого партнёра: политика базы пустит его к строке,
    # и не пускает её в контекст rs-dev только явное условие в коде.
    role = sql.execute(
        """insert into roles (tenant_id, code, title, permissions, visible_ledgers)
           select %s, code, title, permissions, visible_ledgers from roles
            where code = 'accountant'
              and tenant_id = (select id from tenants where code = 'rs-dev')
           returning id""",
        (other,),
    ).fetchone()[0]
    sql.execute(
        """insert into memberships (tenant_id, user_id, role_id)
           select %s, m.user_id, %s from memberships m
            join roles r on r.id = m.role_id
           where r.code = 'accountant'
             and m.tenant_id = (select id from tenants where code = 'rs-dev')""",
        (other, role),
    )
    yield {"line": str(line), "document": str(document)}
    sql.execute("delete from memberships where tenant_id = %s", (other,))
    sql.execute("delete from roles where tenant_id = %s", (other,))
    # По строке, а не по партнёру: ошибка, которую ловят тесты ниже, завела бы
    # связь под чужим партнёром — уборка не должна от неё споткнуться.
    sql.execute(
        "delete from bank_line_links where line_id in "
        "(select id from bank_lines where tenant_id = %s)", (other,),
    )
    sql.execute("delete from facts where dedup_key = %s", (f"bank:line:{line}",))
    sql.execute("delete from bank_lines where tenant_id = %s", (other,))
    sql.execute("delete from bank_statements where tenant_id = %s", (other,))
    sql.execute("delete from source_documents where tenant_id = %s", (other,))
    sql.execute("delete from periods where tenant_id = %s", (other,))
    sql.execute("delete from tenants where id = %s", (other,))


def test_a_line_of_another_partner_is_not_found(accountant, foreign):
    answer = accountant.post(f"/statement/lines/{foreign['line']}/", {"action": "inbox"})
    assert answer.status_code == 404
    assert accountant.get(MATCH + f"?line={foreign['line']}").status_code == 404


def test_an_invoice_of_another_partner_is_not_paid(accountant, sql, foreign):  # noqa: F811
    upload(accountant, "12.07.2026;1.000,00;;Neko;x\n")
    line_id = lines(sql)[0][0]
    answer = accountant.post(f"/statement/lines/{line_id}/",
                             {"action": "pay", "invoice": foreign["document"]})
    assert answer.status_code == 400
    # Отказ — «не найден», как на выдуманный номер (D023), а не какой-то
    # другой: у пустого чужого счёта нашлась бы и своя причина отказать.
    assert re.findall(r'data-error="1">([^<]*)', body(answer)) == ["Счёт не найден."]
    assert lines(sql)[0][4] is None
    assert facts_like(sql, "manual:payment:%") == []


def test_a_message_in_the_address_is_not_shown(accountant):
    """Сообщение продукта не подделывается ссылкой."""
    shown = body(accountant.get(MATCH + "?error=Позвоните+по+номеру&done=hacked"))
    # Ссылки и переключатель языка честно несут адрес дальше — экранированным
    # атрибутом. Проверяется текст страницы: в нём ни чужой фразы, ни чужого кода.
    assert 'data-error="1"' not in shown
    text = re.sub(r'="[^"]*"', '=""', shown)
    assert "Позвоните" not in text
    assert "hacked" not in text
