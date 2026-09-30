"""«Вся сеть» — явный набор точек, а не отсутствие набора (T221, D085).

Правило владельца (D085, таблица случаев issue #194): набор точек задан — он
решает, куда идут деньги человека; одна точка — целиком на неё; несколько —
делятся; «вся сеть» — делится между всеми точками партнёра общим правилом
«поровну»; набор не задан вовсе — старое поведение, точка строки табеля.

До этой миграции «вся сеть» и «не задан» были одним и тем же — нет строк
привязки. Поэтому офисный человек, отмеченный в табеле на пиццерии, ложился на
неё целиком: ровно исходная дыра #194. Теперь «вся сеть» — строка привязки без
точки. Расчёт (`payrun.posting._shares`) читает её как единственную «точку»
человека и пишет факт без точки, а его разносит общее правило (`0270`).

**Доступ не расширяется.** Строка без точки по `app_unit_is_visible` видна
всем (`0011`: «ничья»). Для привязки это означало бы две вещи сразу:

* управляющий точки начал бы видеть каждого офисного человека — имя и
  условия найма, — хотя сетевые затраты урезанной роли не показываются (T130);
* политика записи пустила бы его завести «вся сеть» своему человеку, то есть
  одним действием снять его затраты со своей точки на всех.

Поэтому строка «вся сеть» видна и пишется только тем, у кого ограничения по
точкам нет вовсе, а в правиле «свой человек» (`0269`) она не участвует.
"""

import core.models
import django.contrib.postgres.constraints
import django.db.models.deletion
from django.db import migrations, models


POLICY = """
drop policy if exists unit_visibility on employee_units;
create policy unit_visibility on employee_units
    as restrictive for all
    using (case when unit_id is null then app_unit_ids(tenant_id) is null
                else app_unit_is_visible(tenant_id, unit_id) end)
    with check (case when unit_id is null then app_unit_ids(tenant_id) is null
                     else app_unit_is_visible(tenant_id, unit_id) end);
"""

# Возврат — ровно текст `0267`.
POLICY_BACK = """
drop policy if exists unit_visibility on employee_units;
create policy unit_visibility on employee_units
    as restrictive for all
    using (app_unit_is_visible(tenant_id, unit_id))
    with check (app_unit_is_visible(tenant_id, unit_id));
"""

FUNCTION = """
create or replace function app_employee_is_visible(p_tenant uuid, p_employee uuid)
returns boolean
language sql stable
as $$
    select app_unit_ids(p_tenant) is null   -- ограничения по точкам нет — виден каждый
        or exists (
            select 1
              from employment_terms t
             where t.employee_id = p_employee
               and t.tenant_id = p_tenant
               and app_unit_is_visible(t.tenant_id, t.unit_id)
        )
        or exists (
            -- Точки, по которым делятся его деньги (D055). Строка «вся сеть»
            -- (без точки) своим никого не делает: иначе каждый офисный был бы
            -- виден каждому управляющему (`0271`).
            select 1
              from employee_units eu
             where eu.employee_id = p_employee
               and eu.tenant_id = p_tenant
               and eu.unit_id is not null
               and app_unit_is_visible(eu.tenant_id, eu.unit_id)
        )
$$;
comment on function app_employee_is_visible(uuid, uuid) is
    'Свой ли это человек: есть ли у него условия найма ИЛИ привязка к точкам на видимой точке (строка «вся сеть» не в счёт). Даты версий не учитываются';
"""

# Возврат — текст `0269` слово в слово.
FUNCTION_BACK = """
create or replace function app_employee_is_visible(p_tenant uuid, p_employee uuid)
returns boolean
language sql stable
as $$
    select app_unit_ids(p_tenant) is null   -- ограничения по точкам нет — виден каждый
        or exists (
            select 1
              from employment_terms t
             where t.employee_id = p_employee
               and t.tenant_id = p_tenant
               and app_unit_is_visible(t.tenant_id, t.unit_id)
        )
        or exists (
            -- Точки, по которым делятся его деньги (D055). Даты версий не
            -- проверяются — тем же доводом, что и у условий найма выше.
            select 1
              from employee_units eu
             where eu.employee_id = p_employee
               and eu.tenant_id = p_tenant
               and app_unit_is_visible(eu.tenant_id, eu.unit_id)
        )
$$;
comment on function app_employee_is_visible(uuid, uuid) is
    'Свой ли это человек: есть ли у него условия найма ИЛИ привязка к точкам на видимой точке. Даты версий не учитываются';
"""

COMMENTS = """
comment on table employee_units is
    'Точки, между которыми делятся деньги человека. Строка без точки — «вся сеть» (D085); строк нет вовсе — набор не задан, деньги идут на точку строки табеля';
comment on column employee_units.unit_id is
    'Точка. Пусто — «вся сеть»: деньги делятся между всеми точками партнёра общим правилом (D085). Такая строка не соседствует с другими в одном периоде';
"""

COMMENTS_BACK = """
comment on table employee_units is
    'Точки, на которых работает человек. Пусто — офис: деньги идут на разнесение общим правилом (D055)';
comment on column employee_units.unit_id is null;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0270_payroll_allocated_to_the_network'),
    ]

    operations = [
        migrations.RunSQL(POLICY, POLICY_BACK),
        migrations.RunSQL(FUNCTION, FUNCTION_BACK),
        migrations.RunSQL(COMMENTS, COMMENTS_BACK),
        migrations.AlterField(
            model_name='employeeunit',
            name='unit',
            field=models.ForeignKey(blank=True, db_column='unit_id', null=True, on_delete=django.db.models.deletion.CASCADE, to='core.unit'),
        ),
        migrations.AddConstraint(
            model_name='employeeunit',
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(expressions=[('employee', '='), (models.Case(models.When(then=models.Value(1), unit__isnull=True), default=models.Value(0), output_field=models.IntegerField()), '<>'), (core.models.DateRange('valid_from', 'valid_to', models.Value('[)')), '&&')], name='employee_units_network_alone'),
        ),
        migrations.AddConstraint(
            model_name='employeeunit',
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(condition=models.Q(('unit__isnull', True)), expressions=[('employee', '='), (core.models.DateRange('valid_from', 'valid_to', models.Value('[)')), '&&')], name='employee_units_one_network'),
        ),
    ]
