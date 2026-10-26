"""Право видеть ведомость и роль регионального управляющего (T236, эталон «Роли и права»).

**`payroll.view` — «Видеть ведомость и суммы расчёта».** До сих пор суммы
расчёта видел каждый, кого завели к партнёру: ведомость резали только регистры
и точки. Эталон делает это отдельной строкой и не даёт её управляющему региона
— «больше точек — не значит больше денег». Без права такую роль не настроить
вовсе.

**Никто ничего не теряет.** Право получает каждая роль, которая уже есть в
базе, — и в наборе, и в снимке формы (`shipped_shape`), тем же приёмом, что в
`0274`: доставка форм (`core/role_delivery.py`) сравнивает базу со снимком, и
роль с выросшим набором при прежнем снимке приняла бы за правленную партнёром.

**Стена — в политике, а не в экране.** На `pay_components`, `payslip_totals`,
`payslip_steps` (след расчёта: применённые значения и входы шага — те же
суммы, разложенные по шагам) и `retro_adjustments` (перенесённая разница —
тоже суммы расчёта) встаёт сужающая (`as restrictive`) политика чтения: строки
видит тот, у кого есть `payroll.view` — или право, которое без чтения сумм
невозможно исполнить: расчёт (`insert … returning` расчёта проходит через
политики чтения), утверждение (проводка в P&L читает компоненты) и перенос
разницы за закрытый месяц. Разрешающей её сделать нельзя: разрешающие
политики складываются через ИЛИ и не сужают ничего (правило 2 блока).
На `facts` та же стена стоит только над строками `source = 'payroll'`: после
утверждения ФОТ и налоги с него проводятся в факты (`payrun.posting`), и P&L
отдавал бы ту же зарплату по точкам, мимо стены расчёта. Остальные факты
(выручка, расходы) этим правом не закрыты — это не суммы расчёта.

Условие — функцией `app_sees_payroll`: её же спрашивает `payrun.retro`, где
суммы считаются движком заново, мимо таблиц, и политика их не видит.

**Роль регионального управляющего** заводится каждому уже поднятому партнёру:
доставка форм правит существующие роли, но новых не заводит. Значения —
снимком на момент миграции, а не импортом из `core.roles`: код уходит вперёд,
миграция остаётся прежней.

**Откат роли регионального управляющего** отказывает, если у роли есть люди:
без политик этой миграции они остались бы с ролью, но без стены вокруг сумм
и без понятного состояния. Сначала людей переводят на другую роль.

**Проверка в конце** — как в `0230` и `0274`: `update` под политиками меняет
ноль строк молча, поэтому проверяется результат, а не оператор.
"""
from django.db import migrations

VIEW = "'[\"payroll.view\"]'::jsonb"

GRANT = f"""
update roles
   set permissions = permissions || {VIEW}
 where not permissions ? 'payroll.view';

update roles
   set shipped_shape = jsonb_set(
           shipped_shape, '{{permissions}}',
           (shipped_shape -> 'permissions') || {VIEW})
 where jsonb_typeof(shipped_shape -> 'permissions') = 'array'
   and not ((shipped_shape -> 'permissions') ? 'payroll.view');

do $$
declare
    stuck int;
begin
    select count(*) into stuck from roles where not permissions ? 'payroll.view';
    if stuck > 0 then
        raise exception
            'право видеть ведомость не доехало до % ролей: строки отрезаны политиками',
            stuck;
    end if;
end $$;
"""

REVOKE = """
update roles set permissions = permissions - 'payroll.view';
update roles
   set shipped_shape = jsonb_set(
           shipped_shape, '{permissions}', (shipped_shape -> 'permissions') - 'payroll.view')
 where jsonb_typeof(shipped_shape -> 'permissions') = 'array';
"""

# Кто видит суммы расчёта: право смотреть — или право, которое без чтения сумм
# не исполнить (см. шапку). Одной функцией на все места: политики таблиц сумм
# и охрана пересчёта в `payrun.retro` обязаны отвечать одинаково.
SUMS_TABLES = ("pay_components", "payslip_totals", "payslip_steps", "retro_adjustments")

POLICIES = """
create or replace function app_sees_payroll(p_tenant uuid)
returns boolean
language sql stable
set search_path = public
as $$
    select app_has_permission(p_tenant, 'payroll.view')
        or app_has_permission(p_tenant, 'payrun.calculate')
        or app_has_permission(p_tenant, 'period.approve')
        or app_has_permission(p_tenant, 'retro.post')
$$;
""" + "".join(
    f"create policy payroll_view on {table} as restrictive for select"
    f" using (app_sees_payroll(tenant_id));\n"
    for table in SUMS_TABLES
) + """
create policy payroll_view on facts as restrictive for select
    using (source <> 'payroll' or app_sees_payroll(tenant_id));
"""

DROP_POLICIES = "drop policy if exists payroll_view on facts;\n" + "".join(
    f"drop policy if exists payroll_view on {table};\n" for table in SUMS_TABLES
) + "drop function if exists app_sees_payroll(uuid);\n"

REGIONAL = """
insert into roles (tenant_id, code, title, permissions, permission_states,
                   visible_ledgers, shipped_shape)
select t.id, 'regional_manager', 'Региональный управляющий',
       '["timesheet.edit", "unit.close"]'::jsonb,
       '{"rules.manage": "never", "roles.manage": "never"}'::jsonb,
       '{official,supplementary}'::ledger[],
       '{"visible_ledgers": ["official", "supplementary"],
         "permissions": ["timesheet.edit", "unit.close"]}'::jsonb
  from tenants t
 where not exists (
           select 1 from roles r where r.tenant_id = t.id and r.code = 'regional_manager'
       );

do $$
declare
    missing int;
begin
    select count(*) into missing
      from tenants t
     where not exists (
               select 1 from roles r where r.tenant_id = t.id and r.code = 'regional_manager'
           );
    if missing > 0 then
        raise exception
            'роль регионального управляющего не заведена у % партнёров: '
            'строки отрезаны политиками', missing;
    end if;
end $$;
"""

DROP_REGIONAL = """
do $$
declare
    members int;
begin
    select count(*) into members
      from memberships m join roles r on r.id = m.role_id
     where r.code = 'regional_manager';
    if members > 0 then
        raise exception
            'у роли регионального управляющего % участников: переведите их на '
            'другую роль, потом откатывайте', members;
    end if;
end $$;

delete from roles r
 where r.code = 'regional_manager'
   and not exists (select 1 from memberships m where m.role_id = r.id);
"""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0274_staff_and_terms_rights"),
    ]

    operations = [
        migrations.RunSQL(GRANT, REVOKE),
        migrations.RunSQL(POLICIES, DROP_POLICIES),
        migrations.RunSQL(REGIONAL, DROP_REGIONAL),
    ]
