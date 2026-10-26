"""Края миграции `0275` и доставки форм после T236.

**Откат роли регионального управляющего с людьми — отказ словами.** Без
политик `0275` такие люди остались бы с ролью, но без стены вокруг сумм.
Откат обязан остановиться и сказать, что делать, а не пройти молча.

**Откат периода уходит у нетронутых ролей и остаётся у правленных.** Право
`period.reopen` больше не входит в форму директора и бухгалтера. Снимает его
доставка форм (`core/role_delivery.py`): нетронутая роль — база равна снимку —
догоняет код, правка партнёра — нет. Проверяется на живой базе ролью
`app_user`, тем же способом, каким сервис спрашивает права.
"""
from __future__ import annotations

import json
import subprocess
import uuid

import psycopg
import pytest

from conftest import run_manage, temp_database
from core.role_delivery import EDITED, normalize, product_shape, sync
from core.roles import ROLE_SHAPES


def _tenant(conn) -> str:
    return conn.execute(
        "insert into tenants (code, title, country_code, base_currency, report_currency)"
        " values ('rs', 'Партнёр', 'RS', 'RSD', 'EUR') returning id::text"
    ).fetchone()[0]


def test_rolling_back_the_regional_role_with_people_is_refused():
    with temp_database("regional_back") as dsn:
        with psycopg.connect(dsn, autocommit=True) as conn:
            tenant = _tenant(conn)
            role = conn.execute(
                "insert into roles (tenant_id, code, title, visible_ledgers, permissions)"
                " values (%s, 'regional_manager', 'Региональный управляющий',"
                "         '{official,supplementary}', '[\"timesheet.edit\"]')"
                " returning id",
                (tenant,),
            ).fetchone()[0]
            user = uuid.uuid4()
            conn.execute(
                "insert into users (id, username, password, full_name)"
                " values (%s, 'reg', '!', 'Рег')",
                (user,),
            )
            conn.execute(
                "insert into memberships (tenant_id, user_id, role_id) values (%s, %s, %s)",
                (tenant, user, role),
            )

        refused = subprocess_result(dsn)
        assert refused.returncode != 0, "откат прошёл, хотя у роли есть люди"
        assert "переведите их на другую роль" in refused.stdout + refused.stderr

        with psycopg.connect(dsn, autocommit=True) as conn:
            assert conn.execute(
                "select count(*) from pg_policies where policyname = 'payroll_view'"
            ).fetchone()[0] > 0, "откат снял стену, хотя остановился"
            conn.execute("delete from memberships")

        done = subprocess_result(dsn)
        assert done.returncode == 0, done.stdout + done.stderr
        with psycopg.connect(dsn, autocommit=True) as conn:
            assert not conn.execute(
                "select count(*) from roles where code = 'regional_manager'"
            ).fetchone()[0]


def subprocess_result(dsn):
    """Откат к `0274` — подпроцессом, как его запустит человек."""
    try:
        return run_manage(dsn, "migrate", "core", "0274_staff_and_terms_rights", "--no-input")
    except subprocess.CalledProcessError as failure:
        return failure


# --- доставка форм снимает откат периода только с нетронутых ролей ------------------


@pytest.fixture
def stand(web_env):
    """Отменяемая транзакция поверх общей базы веб-тестов (как в test_roles_delivery)."""
    from django.db import connection, transaction

    with transaction.atomic():
        yield connection
        transaction.set_rollback(True)


def _put(conn, code: str, permissions: list[str], shipped: list[str]) -> None:
    ledgers = list(ROLE_SHAPES[code].ledgers)
    with conn.cursor() as cur:
        cur.execute(
            "update roles set permissions = %s::jsonb, shipped_shape = %s::jsonb"
            " where code = %s and tenant_id is not null",
            [
                json.dumps(permissions),
                json.dumps({"visible_ledgers": ledgers, "permissions": shipped}),
                code,
            ],
        )


def _may_reopen(conn, code: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "select u.id::text, r.tenant_id::text from users u"
            "  join memberships m on m.user_id = u.id join roles r on r.id = m.role_id"
            " where r.code = %s and r.tenant_id is not null limit 1",
            [code],
        )
        user, tenant = cur.fetchone()
        cur.execute("set local role app_user")
        cur.execute("select set_config('app.user_id', %s, true)", [user])
        try:
            cur.execute("select app_has_permission(%s, 'period.reopen')", [tenant])
            return cur.fetchone()[0]
        finally:
            cur.execute("reset role")
            cur.execute("select set_config('app.user_id', '', true)")


def test_the_delivery_takes_reopen_from_untouched_roles_and_keeps_partner_edits(stand):
    # Директор — как его поставил продукт до T236: откат и в базе, и в снимке.
    before = sorted(set(ROLE_SHAPES["director"].permissions) | {"period.reopen"})
    _put(stand, "director", before, before)
    # Бухгалтер — партнёр поправил руками: снял `retro.post`, откат оставил.
    shipped = sorted(set(ROLE_SHAPES["accountant"].permissions) | {"period.reopen"})
    edited = [code for code in shipped if code != "retro.post"]
    _put(stand, "accountant", edited, shipped)
    assert _may_reopen(stand, "director") and _may_reopen(stand, "accountant"), (
        "проверять нечего: отката у ролей нет до доставки"
    )

    report = sync(stand, apply=True)

    assert "director" in [item.code for item in report.delivered]
    assert not _may_reopen(stand, "director"), "у нетронутого директора откат остался"
    assert "accountant" in [item.code for item in report.by_state(EDITED)]
    assert _may_reopen(stand, "accountant"), "доставка затёрла правку партнёра"
    with stand.cursor() as cur:
        cur.execute("select permissions from roles where code = 'director'"
                    " and tenant_id is not null")
        raw = cur.fetchone()[0]
    permissions = json.loads(raw) if isinstance(raw, str) else raw
    assert normalize(ROLE_SHAPES["director"].ledgers, permissions) == product_shape("director")
