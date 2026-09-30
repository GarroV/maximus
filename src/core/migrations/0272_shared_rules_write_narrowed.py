"""Запись общих правил и привязок к точкам сужена (T221).

**Общее правило разнесения** (пустой `tenant_id`, `0270`) читает каждый
вошедший — так и задумано: правило поставляет продукт, и найтись оно обязано у
каждого партнёра. Править и удалять её не должен никто из партнёров — теперь
это закреплено отдельными ограничительными политиками на правку и удаление, а
не выводится из политики изоляции. Заводить общие строки партнёр не мог и раньше.

**Два общих правила одной строки** не уживаются: исключение сравнивало
партнёра через `=`, а `null = null` не совпадает никогда. Пустой партнёр
теперь сравнивается как отдельный (`SHARED_TENANT` в `core.models`).

**Привязка к точке** делает человека «своим» (`0269`), поэтому запись её
проверяет не только точку, но и человека: привязывать можно лишь того, кого
роль и так видит. Роли без ограничения по точкам видно всех, для неё
ничего не меняется.
"""

import core.models
import django.contrib.postgres.constraints
import django.db.models.functions.comparison
import uuid
from django.db import migrations, models


POLICIES = """
create policy shared_rows_read_only_update on allocation_rules
    as restrictive for update
    using (tenant_id is not null)
    with check (tenant_id is not null);

create policy shared_rows_read_only_delete on allocation_rules
    as restrictive for delete
    using (tenant_id is not null);

create policy person_visible_insert on employee_units
    as restrictive for insert
    with check (app_employee_is_visible(tenant_id, employee_id));

create policy person_visible_update on employee_units
    as restrictive for update
    using (app_employee_is_visible(tenant_id, employee_id))
    with check (app_employee_is_visible(tenant_id, employee_id));
"""

POLICIES_BACK = """
drop policy if exists person_visible_update on employee_units;
drop policy if exists person_visible_insert on employee_units;
drop policy if exists shared_rows_read_only_delete on allocation_rules;
drop policy if exists shared_rows_read_only_update on allocation_rules;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0271_employee_units_network'),
    ]

    operations = [
        migrations.RunSQL(POLICIES, POLICIES_BACK),
        migrations.RemoveConstraint(
            model_name='allocationrule',
            name='allocation_rules_line_no_overlap',
        ),
        migrations.AddConstraint(
            model_name='allocationrule',
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(condition=models.Q(('counterparty__isnull', True), ('expense_item__isnull', True)), expressions=[(django.db.models.functions.comparison.Coalesce('tenant', models.Value(uuid.UUID('00000000-0000-0000-0000-000000000000')), output_field=models.UUIDField()), '='), ('pnl_item', '='), (core.models.DateRange('valid_from', 'valid_to', models.Value('[)')), '&&')], name='allocation_rules_line_no_overlap'),
        ),
    ]
