"""ФОТ сетевого человека доезжает до точек, а не висит вечно (T221, D055, #194).

Решение владельца: «офис на всех работает, вне зависимости» — затраты человека
без точек делятся между всеми точками сети, умолчание поровну. Перенос ФОТ в
факты (T208) это и делал: у сетевого человека точки нет, факт пишется
`pending` — «разнесёт правило разнесения, как любой расход юрлица».

Правило не находилось никогда. `allocation_rule_for` ищет его по контрагенту
**или** по статье расхода, а у зарплатного факта нет ни того, ни другого: он
приходит не от поставщика и не из кассы. План выходил пустым, факт оставался
`pending` — то есть в затраты точек ФОТ офиса не попадал вовсе. Проверено
прогоном на июне 2026: три строки на 46 548,55 повисли неразнесёнными
(`test_the_office_payroll_does_not_hang_undistributed`).

**Третий ключ правила — строка P&L.** Пустые контрагент и статья означают:
правило адресуется самой строкой отчёта. Это ровно тот случай, ради которого
заводился второй ключ (`0233`): «у расхода из кассы контрагента нет и взяться
ему неоткуда». У зарплаты нет и статьи — статьи заводит партнёр под свои траты,
а зарплата приходит из расчёта. Регистр в этот ключ не входит: строка отчёта уже
названа, а набор точек сети от регистра не зависит (у поставщика было иначе —
там регистр различает официальную оплату и кассовую, то есть два разных правила).

**Правило поставляется продуктом, а не заводится у каждого партнёра.** Строка с
пустым `tenant_id` — та же конструкция, что у общего справочника строк P&L и
системных ролей (`SHARED_ROW_TABLES`, `0004_rls`): видна каждому, кто вошёл,
правится только миграцией. Иначе «поровну» пришлось бы заводить в каждом месте,
где рождается тенант — сид разработки, сид демо, создание пространства, — и
четвёртый такой путь молча остался бы без правила, а узналось бы это через месяц
по висящей сумме. Своя строка партнёра перебивает общую: она найдётся первой.

**Отдельной настройки способа деления этой миграцией не появляется.** Умолчание
«поровну» владелец назвал прямо, а второй способ («по выручке») заработает
вместе с коннектором Dodo IS. Где жить настройке — у партнёра или у группы —
открытый пункт самого issue #194, и решается он владельцем, а не здесь.
"""
import django.contrib.postgres.constraints
import django.db.models.deletion
from django.db import migrations, models

import core.models

# Общая строка обязана быть видна каждому, кто вошёл, — иначе продукт поставляет
# правило, которого никто не может применить. Два условия, потому что политик
# две: изоляция по партнёру и видимость регистра. Вторая для общей строки
# бессмысленна вовсе: регистр партнёрский, а строка ничья.
POLICIES = """
drop policy if exists tenant_isolation on allocation_rules;
create policy tenant_isolation on allocation_rules
    for all
    using (tenant_id in (select app_tenant_ids())
           or (tenant_id is null and app_user_id() is not null))
    with check (tenant_id in (select app_tenant_ids()));

drop policy if exists ledger_visibility on allocation_rules;
create policy ledger_visibility on allocation_rules
    as restrictive for select
    using (tenant_id is null or ledger = any (app_visible_ledgers(tenant_id)));
"""

POLICIES_BACK = """
drop policy if exists tenant_isolation on allocation_rules;
create policy tenant_isolation on allocation_rules
    for all
    using (tenant_id in (select app_tenant_ids()))
    with check (tenant_id in (select app_tenant_ids()));

drop policy if exists ledger_visibility on allocation_rules;
create policy ledger_visibility on allocation_rules
    as restrictive for select
    using (ledger = any (app_visible_ledgers(tenant_id)));
"""

RULE_FOR = """
create or replace function allocation_rule_for(p_fact_id uuid)
returns allocation_rules
language plpgsql stable
as $$
declare
    f facts;
    r allocation_rules;
begin
    select * into f from facts where id = p_fact_id;
    if not found then
        return null;
    end if;

    if f.counterparty_id is null and f.expense_item_id is null then
        -- Ключа у факта нет ни одного: так приходит зарплата сетевого человека
        -- (D055). Ключом остаётся строка P&L. Регистр в поиск не входит —
        -- довод в шапке миграции. Своя строка партнёра ищется первой: общая
        -- поставляется продуктом и обязана уступать.
        select * into r
          from allocation_rules ar
         where (ar.tenant_id = f.tenant_id or ar.tenant_id is null)
           and ar.counterparty_id is null
           and ar.expense_item_id is null
           and ar.pnl_item_id = f.pnl_item_id
           and ar.valid_from <= f.period
           and (ar.valid_to is null or ar.valid_to > f.period)
         order by (ar.tenant_id is null), ar.valid_from desc
         limit 1;
        if not found then
            return null;
        end if;
        return r;
    end if;

    -- Правило действует на период учёта, а не на дату документа: отчёт строится
    -- по периоду, разнесение должно жить в той же логике.
    --
    -- Контрагент старше: он приходит с фактурой, где статью человек не выбирал.
    -- Статья — ключ ручного расхода, у которого контрагента нет вовсе.
    --
    -- Регистр входит в поиск наравне с ключом. Так устроено само правило:
    -- ограничения непересечения разрешают одной статье и одному поставщику по
    -- два действующих правила — по одному на регистр, — потому что одна и та же
    -- трата бывает и официальной, и из кассы, и это разные строки P&L. Без
    -- условия по регистру `limit 1` выбирал бы произвольное из двух и молчал.
    select * into r
      from allocation_rules ar
     where ar.tenant_id = f.tenant_id
       and ar.ledger = f.ledger
       and (
             (f.counterparty_id is not null and ar.counterparty_id = f.counterparty_id)
             or (f.counterparty_id is null and ar.expense_item_id = f.expense_item_id)
           )
       and ar.valid_from <= f.period
       and (ar.valid_to is null or ar.valid_to > f.period)
     order by ar.valid_from desc
     limit 1;

    if not found then
        return null;
    end if;
    return r;
end $$;

comment on function allocation_rule_for(uuid) is
    'Правило разнесения, действующее для факта. Ключ — контрагент, статья расхода или сама строка P&L: тот, который у факта есть';
"""

# Возврат — дословно тело из `0236`: пересказ по памяти разошёлся бы с ним, и
# откат оставил бы третью версию поиска правила.
RULE_FOR_BACK = """
create or replace function allocation_rule_for(p_fact_id uuid)
returns allocation_rules
language plpgsql stable
as $$
declare
    f facts;
    r allocation_rules;
begin
    select * into f from facts where id = p_fact_id;
    if not found then
        return null;
    end if;
    if f.counterparty_id is null and f.expense_item_id is null then
        return null;    -- разносить не по чему: ни контрагента, ни статьи
    end if;

    select * into r
      from allocation_rules ar
     where ar.tenant_id = f.tenant_id
       and ar.ledger = f.ledger
       and (
             (f.counterparty_id is not null and ar.counterparty_id = f.counterparty_id)
             or (f.counterparty_id is null and ar.expense_item_id = f.expense_item_id)
           )
       and ar.valid_from <= f.period
       and (ar.valid_to is null or ar.valid_to > f.period)
     order by ar.valid_from desc
     limit 1;

    if not found then
        return null;
    end if;
    return r;
end $$;

comment on function allocation_rule_for(uuid) is
    'Правило разнесения, действующее для факта. Один поиск на план и на объяснение: две копии разошлись бы молча';
"""

# Сами правила. Дата начала — заведомо раньше любых данных продукта: правило
# «поровну» не вводится с какого-то числа, оно и есть поведение по умолчанию, и
# закрытый месяц, посчитанный до этой миграции, обязан разноситься так же.
#
# Строки P&L берутся кодами из общего справочника (`tenant_id is null`) — теми
# же, что пишет перенос зарплаты (`payrun.posting.LABOUR`, `TAXES`). Нет строки
# в справочнике — правила не появится: молча привязать зарплату к выдуманной
# строке хуже, чем не разнести.
DEFAULTS = """
insert into allocation_rules (id, tenant_id, pnl_item_id, method, ledger, valid_from)
select gen_random_uuid(), null, pi.id, 'even', 'official', date '2000-01-01'
  from pnl_items pi
 where pi.tenant_id is null
   and pi.code in ('labour_cost', 'payroll_taxes')
   and not exists (
        select 1 from allocation_rules ar
         where ar.tenant_id is null and ar.pnl_item_id = pi.id
           and ar.counterparty_id is null and ar.expense_item_id is null
   );
"""

DEFAULTS_BACK = """
delete from allocation_rules
 where tenant_id is null and counterparty_id is null and expense_item_id is null
   and pnl_item_id in (
        select id from pnl_items
         where tenant_id is null and code in ('labour_cost', 'payroll_taxes')
   );
"""


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0269_employee_visible_by_units'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='allocationrule',
            name='allocation_rules_one_key',
        ),
        migrations.AlterField(
            model_name='allocationrule',
            name='tenant',
            field=models.ForeignKey(blank=True, db_column='tenant_id', null=True, on_delete=django.db.models.deletion.CASCADE, to='core.tenant'),
        ),
        migrations.AddConstraint(
            model_name='allocationrule',
            constraint=models.CheckConstraint(condition=models.Q(('counterparty__isnull', True), ('expense_item__isnull', True), _connector='OR'), name='allocation_rules_one_key'),
        ),
        migrations.AddConstraint(
            model_name='allocationrule',
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(condition=models.Q(('counterparty__isnull', True), ('expense_item__isnull', True)), expressions=[('tenant', '='), ('pnl_item', '='), (core.models.DateRange('valid_from', 'valid_to', models.Value('[)')), '&&')], name='allocation_rules_line_no_overlap'),
        ),
        migrations.RunSQL(POLICIES, POLICIES_BACK),
        migrations.RunSQL(RULE_FOR, RULE_FOR_BACK),
        migrations.RunSQL(DEFAULTS, DEFAULTS_BACK),
    ]
