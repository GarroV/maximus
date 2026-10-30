"""Narrow metadata access for the platform console; customer facts stay isolated."""
import django.db.models.deletion
from django.db import migrations, models

SQL = """
create policy legal_entities_platform_read on legal_entities for select using (app_is_platform_admin());
create policy legal_entities_platform_create on legal_entities for insert with check (app_is_platform_admin());
create policy legal_entities_platform_update on legal_entities for update using (app_is_platform_admin()) with check (app_is_platform_admin());
create policy units_platform_read on units for select using (app_is_platform_admin());
drop policy unit_visibility on units;
create policy unit_visibility on units as restrictive for all
using (app_is_platform_admin() or app_unit_is_visible(tenant_id, id))
with check (app_is_platform_admin() or app_unit_is_visible(tenant_id, id));

drop policy directory_manage_insert on legal_entities;
drop policy directory_manage_update on legal_entities;
create policy directory_manage_insert on legal_entities as restrictive for insert
with check (app_has_permission(tenant_id, 'directory.manage') or app_is_platform_admin());
create policy directory_manage_update on legal_entities as restrictive for update
with check (app_has_permission(tenant_id, 'directory.manage') or app_is_platform_admin());

alter table platform_events enable row level security;
alter table platform_events force row level security;
create policy platform_events_read on platform_events for select using (app_is_platform_admin());
create policy platform_events_insert on platform_events for insert with check (app_is_platform_admin() and actor_user_id = app_user_id());
grant select, insert on platform_events to app_user;
revoke update, delete on platform_events from app_user;
comment on table platform_events is 'Неизменяемая история административных операций платформы, без секретов и финансовых данных';
"""
BACK = """
drop policy units_platform_read on units;
drop policy unit_visibility on units;
create policy unit_visibility on units as restrictive for all
using (app_unit_is_visible(tenant_id, id)) with check (app_unit_is_visible(tenant_id, id));
drop policy legal_entities_platform_update on legal_entities;
drop policy legal_entities_platform_create on legal_entities;
drop policy legal_entities_platform_read on legal_entities;
drop policy directory_manage_insert on legal_entities;
drop policy directory_manage_update on legal_entities;
create policy directory_manage_insert on legal_entities as restrictive for insert
with check (app_has_permission(tenant_id, 'directory.manage'));
create policy directory_manage_update on legal_entities as restrictive for update
with check (app_has_permission(tenant_id, 'directory.manage'));
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0279_users_google")]
    operations = [
        migrations.CreateModel(
            name="PlatformEvent",
            fields=[
                ("id", models.UUIDField(db_default=models.Func(function="gen_random_uuid"), primary_key=True, serialize=False)),
                ("at", models.DateTimeField(db_default=models.Func(function="now"))),
                ("actor_user_id", models.UUIDField()),
                ("action", models.TextField()),
                ("target", models.TextField()),
                ("reason", models.TextField(db_default="")),
                ("tenant", models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to="core.tenant")),
            ],
            options={"db_table": "platform_events"},
        ),
        migrations.RunSQL(SQL, BACK),
    ]
