"""Platform metadata policy must work as app_user and keep customer facts closed."""
from uuid import uuid4

import psycopg
import pytest

from conftest import T2, USER_ACCOUNTANT, as_app_user
from test_platform_admin_access import platform_admin  # noqa: F401

pytestmark = pytest.mark.usefixtures("db", "platform_admin")


def test_metadata_access_is_narrow_and_events_are_append_only(db, platform_admin):  # noqa: F811
    entity, unit = uuid4(), uuid4()
    db.execute("insert into legal_entities (id, tenant_id, title) values (%s,%s,'Console org')",
               (entity, T2))
    db.execute("insert into units (id, tenant_id, code, title) values (%s,%s,'console','Console')",
               (unit, T2))
    try:
        with as_app_user(db, platform_admin) as conn:
            assert conn.execute("select id from legal_entities where id=%s", (entity,)).fetchone()
            assert conn.execute("select id from units where id=%s", (unit,)).fetchone()
            assert conn.execute("select count(*) from employees where tenant_id=%s",
                                (T2,)).fetchone()[0] == 0
            conn.execute("update legal_entities set tax_number='123' where id=%s", (entity,))
            event = conn.execute(
                "insert into platform_events (actor_user_id, action, target) "
                "values (%s,'test','metadata') returning id", (platform_admin,),
            ).fetchone()[0]
        with as_app_user(db, USER_ACCOUNTANT) as conn:
            assert not conn.execute("select id from legal_entities where id=%s",
                                    (entity,)).fetchone()
            assert not conn.execute("select id from platform_events where id=%s",
                                    (event,)).fetchone()
        with as_app_user(db, platform_admin) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute("delete from platform_events where id=%s", (event,))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute("insert into platform_events (actor_user_id, action, target) "
                                 "values (%s,'spoof','actor')", (USER_ACCOUNTANT,))
    finally:
        db.execute("delete from platform_events where target='metadata'")
        db.execute("delete from units where id=%s", (unit,))
        db.execute("delete from legal_entities where id=%s", (entity,))
