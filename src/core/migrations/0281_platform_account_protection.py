"""Check protected account status without exposing other operators' bootstrap notes."""
from django.db import migrations

SQL = """
create function app_platform_account_protected(p_user uuid)
returns boolean language sql stable security definer
set search_path = public
as $$
    select app_is_platform_admin()
       and exists (select 1 from platform_admins where user_id = p_user)
$$;
revoke all on function app_platform_account_protected(uuid) from public;
grant execute on function app_platform_account_protected(uuid) to app_user;
comment on function app_platform_account_protected(uuid) is
    'Only a platform operator can check whether an account is protected. No bootstrap rows or notes are exposed';
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0280_platform_console")]
    operations = [migrations.RunSQL(SQL, "drop function app_platform_account_protected(uuid);")]
