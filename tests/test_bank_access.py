"""Кому видна банковская выписка и кто вправе её трогать (T198, миграция `0276`).

Всё гоняется **ролью `app_user`**: владелец таблиц обходит `force row level
security`, и тот же набор проверок владельцем был бы зелёным при снятых
политиках.

Утверждения:

1. выписку видит и пишет только тот, у кого есть право разбора первички
   (`suppliers.classify`) — у управляющего точки его нет нарочно (T174), и
   поступления всей компании ему не показываются ни строкой, ни числом;
2. чужой партнёр не виден и не пишется;
3. строку выписки не удалить и не завести второй оригинал с тем же ключом —
   повторная загрузка не удваивает деньги на уровне базы, а не только кода;
4. один платёж не закрывает две строки выписки.
"""
from __future__ import annotations

from contextlib import contextmanager

import psycopg
import pytest

from conftest import (
    R_ACCOUNTANT,
    T1,
    T2,
    USER_ACCOUNTANT,
    USER_MANAGER,
    USER_OTHER,
    as_app_user,
)


@contextmanager
def refused(conn, error):
    """Отказ базы внутри точки сохранения: транзакция теста остаётся живой."""
    with pytest.raises(error), conn.transaction():
        yield


def grant_classify(conn, role: str = R_ACCOUNTANT) -> None:
    """Право разбора — бухгалтеру фикстуры. Откатывается вместе с тестом."""
    conn.execute(
        "update roles set permissions = permissions || '[\"suppliers.classify\"]'::jsonb "
        "where id = %s",
        (role,),
    )


def statement(conn, tenant: str = T1, content_hash: str = "h1") -> str:
    return str(conn.execute(
        """insert into bank_statements (tenant_id, format, content_hash)
           values (%s, 'camt053', %s) returning id""",
        (tenant, content_hash),
    ).fetchone()[0])


def line(conn, statement_id: str, tenant: str = T1, key: str = "k1", duplicate_of=None) -> str:
    return str(conn.execute(
        """insert into bank_lines
                (tenant_id, statement_id, line_no, op_date, direction, amount, dedup_key,
                 duplicate_of)
           values (%s, %s, 1, '2026-06-12', 'out', 24000.00, %s, %s) returning id""",
        (tenant, statement_id, key, duplicate_of),
    ).fetchone()[0])


@pytest.fixture
def lines(db):
    """По строке у своего партнёра и у чужого — заводит владелец схемы."""
    grant_classify(db)
    own = line(db, statement(db))
    foreign = line(db, statement(db, tenant=T2), tenant=T2)
    return {"own": own, "foreign": foreign}


def visible(conn) -> set[str]:
    return {str(r[0]) for r in conn.execute("select id from bank_lines").fetchall()}


def test_the_accountant_with_the_right_sees_only_her_partner(db, lines):
    with as_app_user(db, USER_ACCOUNTANT):
        assert visible(db) == {lines["own"]}
        assert db.execute("select count(*) from bank_statements").fetchone()[0] == 1


def test_the_unit_manager_sees_no_statement_at_all(db, lines):
    with as_app_user(db, USER_MANAGER):
        assert visible(db) == set()
        assert db.execute("select count(*) from bank_statements").fetchone()[0] == 0


def test_without_the_right_even_the_accountant_sees_nothing(db):
    """Право, а не роль: снимите его — выписка пропадает и у бухгалтера."""
    line(db, statement(db))
    with as_app_user(db, USER_ACCOUNTANT):
        assert visible(db) == set()


def test_a_statement_is_not_written_without_the_right(db):
    with as_app_user(db, USER_MANAGER), refused(db, psycopg.errors.InsufficientPrivilege):
        statement(db)


def test_a_statement_is_not_written_into_another_partner(db):
    grant_classify(db)
    with as_app_user(db, USER_ACCOUNTANT), refused(db, psycopg.errors.InsufficientPrivilege):
        statement(db, tenant=T2)


def test_the_other_partner_does_not_see_ours(db, lines):
    with as_app_user(db, USER_OTHER):
        assert lines["own"] not in visible(db)


def test_a_bank_line_cannot_be_deleted(db, lines):
    """Удалённая строка вернулась бы новой при следующей загрузке."""
    with as_app_user(db, USER_ACCOUNTANT), refused(db, psycopg.errors.InsufficientPrivilege):
        db.execute("delete from bank_lines where id = %s", (lines["own"],))


def test_the_same_key_cannot_have_two_originals(db, lines):
    own_statement = db.execute(
        "select statement_id from bank_lines where id = %s", (lines["own"],)
    ).fetchone()[0]
    with as_app_user(db, USER_ACCOUNTANT):
        # Дубль со ссылкой на оригинал ложится…
        line(db, own_statement, key="k1", duplicate_of=lines["own"])
        # …а второй оригинал с тем же ключом — нет.
        with refused(db, psycopg.errors.UniqueViolation):
            line(db, own_statement, key="k1")


def test_one_payment_closes_one_bank_line(db, lines):
    own_statement = db.execute(
        "select statement_id from bank_lines where id = %s", (lines["own"],)
    ).fetchone()[0]
    with as_app_user(db, USER_ACCOUNTANT):
        second = line(db, own_statement, key="k2")
        link = ("insert into bank_line_links (tenant_id, line_id, fact_key, how) "
                "values (%s, %s, 'manual:payment:x', 'manual')")
        db.execute(link, (T1, lines["own"]))
        with refused(db, psycopg.errors.UniqueViolation):
            db.execute(link, (T1, second))


def test_not_ours_needs_a_reason(db, lines):
    grant_classify(db)
    with as_app_user(db, USER_ACCOUNTANT), refused(db, psycopg.errors.CheckViolation):
        db.execute("update bank_lines set not_ours_at = now(), not_ours_why = '' "
                   "where id = %s", (lines["own"],))
