"""Курсы для консолидации сети: версии, источник и курс, закреплённый за месяцем (T209).

**Что здесь появляется.** Эталон модуля 5 сводит сеть партнёров в одну валюту
«по курсу на последний день периода» и обещает: курс «хранится вместе с
отчётом. Пересчёт задним числом не меняет уже сохранённую консолидацию — иначе
июньская цифра менялась бы каждый день вслед за курсом».

**Справочник курсов только дописывается.** Строка `fx_rates` не правится и не
удаляется ни приложением, ни владельцем схемы (триггер, а не политика: правило о
данных, а не о правах). Исправленный курс — новая строка с той же датой: она
действует с момента ввода. Заводит курс администратор платформы — справочник
общий для всех партнёров, и партнёр, правящий его, двигал бы цифру соседу.
Откуда берутся курсы, не решено (ручной ввод или лента), поэтому у строки есть
`note` — источник словами — и кто/когда ввёл.

**Курс задаётся так, как его публикуют.** «1 EUR = 117,1834 RSD» хранится как
есть; пересчёт RSD → EUR делит на курс, а не умножает на обратный — обратное
число с восемью знаками теряло бы копейки на крупных суммах.

**Закрытый месяц держит ссылку на строку курса** (`period_fx_rates`). При
закрытии месяца (триггер на `periods`) для валюты учёта и каждой валюты, у
которой есть курс к валюте консолидации, запоминается, какая строка
справочника была курсом на конец месяца. Факты при этом не читаются: функция
видела бы их глазами закрывающего. Валюту, курса к которой ещё нет, закрепит
за уже закрытыми месяцами её первый курс. Строка справочника неизменна, поэтому закрытый месяц пересчитывается
в то же число сколько угодно раз: новый курс задним числом его не трогает.
Открытие месяца (откат расчёта, D021) ссылки снимает: месяц снова считается
по живому курсу и при следующем закрытии закрепит тот, что действует тогда.

**Курса на день закрытия могло не быть.** Тогда ссылка пустая, и отчёт говорит
«нет курса», а не ноль. Первый введённый подходящий курс её заполняет — один
раз; дальше она неизменна, как и заполненная при закрытии.

**Закрепление не зависит от контекста пользователя.** При накате миграции и
при вводе курса администратором платформы партнёра в контексте нет, а `force`
закрыл бы владельцу схемы `tenants` и `periods`: уже закрытые месяцы молча
остались бы без курса. Поэтому владельцу — политики только на чтение этих двух
таблиц (проверяет `test_consolidation_plain_owner`).

**`fill_report_amounts` и `fx_rate_on` (из `0230`) сняты:** первая приколачивала
сумму к факту мимо закреплённого курса.

**`pnl_lines.amount_report` считает через то же правило** (`report_amount`):
закреплённый курс у закрытого месяца, живой — у открытого. Одно правило на
продукт — иначе экран консолидации и выгрузки разошлись бы на закрытом месяце.
"""

from importlib import import_module

import django.db.models.deletion
from django.db import migrations, models

# --- справочник курсов: только дописывается ----------------------------------
FX_RATES = """
alter table fx_rates add constraint fx_rates_codes
    check (base_currency ~ '^[A-Z]{3}$' and quote_currency ~ '^[A-Z]{3}$');
alter table fx_rates alter column entered_by set default app_user_id();

comment on column fx_rates.rate is
    '1 base_currency = rate quote_currency — так, как курс публикует источник';
comment on column fx_rates.note is
    'Откуда взят курс, словами: источник курсов не выбран, поэтому он записывается при вводе';
comment on column fx_rates.entered_by is 'Кто ввёл курс';
comment on column fx_rates.entered_at is
    'Когда введён. Из двух курсов одной даты действует введённый позже';

create or replace function fx_rates_append_only()
returns trigger
language plpgsql
as $$
begin
    raise exception 'курс не правится и не удаляется: введите новый с той же датой'
        using hint = 'закрытые месяцы ссылаются на строку справочника, и правка на месте сдвинула бы их';
end
$$;

comment on function fx_rates_append_only() is
    'Справочник курсов только дописывается: закрытые месяцы ссылаются на его строки';

create trigger fx_rates_append_only
    before update or delete on fx_rates
    for each row execute function fx_rates_append_only();

-- Заводит курс только администратор платформы и только от своего имени.
create policy platform_admin_enters on fx_rates
    for insert
    with check (app_is_platform_admin() and entered_by = app_user_id());

revoke all on fx_rates from app_user;
grant select, insert on fx_rates to app_user;

-- Закрепление курса идёт владельцем схемы (`security definer`), в том числе при
-- накате без контекста пользователя; `force` закрыл бы ему справочник целиком,
-- и месяц закрепил бы пустоту. Справочник общий и не секретный.
do $$
begin
    execute format(
        'create policy owner_reads on fx_rates for select to %I using (true)', current_user
    );
end
$$;
"""

FX_RATES_BACK = """
revoke all on fx_rates from app_user;
grant select on fx_rates to app_user;
drop policy if exists owner_reads on fx_rates;
drop policy if exists platform_admin_enters on fx_rates;
drop trigger if exists fx_rates_append_only on fx_rates;
drop function if exists fx_rates_append_only();
alter table fx_rates alter column entered_by drop default;
alter table fx_rates drop constraint if exists fx_rates_codes;
"""

# --- поиск и пересчёт ---------------------------------------------------------
LOOKUP = """
-- Строка справочника, действующая на дату: последняя дата не позже нужной, из
-- двух строк одной даты — введённая позже. Пара ищется в обе стороны: курс
-- вводят так, как его публикуют, а не так, как удобно считать.
create or replace function fx_rate_row_on(p_from text, p_to text, p_on date)
returns uuid
language sql stable
as $$
    select id
      from fx_rates
     where ((base_currency = p_from and quote_currency = p_to)
         or (base_currency = p_to and quote_currency = p_from))
       and rate_date <= p_on
     order by rate_date desc, entered_at desc, (base_currency = p_from) desc
     limit 1
$$;

comment on function fx_rate_row_on(text, text, date) is
    'Строка курса, действующая на дату: последняя дата не позже нужной, из двух одной даты — введённая позже';

-- Пересчёт суммы по строке курса. Обратная пара делится, а не умножается на
-- обратное число: так сумма совпадает с пересчётом «на калькуляторе».
create or replace function fx_convert(p_amount numeric, p_rate_id uuid, p_from text)
returns numeric
language sql stable
as $$
    select round(case when r.base_currency = p_from then p_amount * r.rate
                      else p_amount / r.rate end, 2)
      from fx_rates r
     where r.id = p_rate_id
$$;

comment on function fx_convert(numeric, uuid, text) is
    'Сумма в другой валюте по строке курса. null — строки нет';

-- Курс, которым считается месяц партнёра: закреплённый, если месяц закрыт,
-- иначе действующий на конец месяца.
create or replace function report_rate_row(p_tenant uuid, p_period date, p_from text, p_to text)
returns uuid
language sql stable
as $$
    select case
             when x.id is not null then x.fx_rate_id
             else fx_rate_row_on(p_from, p_to, (p_period + interval '1 month - 1 day')::date)
           end
      from (select 1) one
      left join period_fx_rates x
             on x.tenant_id = p_tenant and x.period = p_period
            and x.currency = p_from and x.report_currency = p_to
$$;

comment on function report_rate_row(uuid, date, text, text) is
    'Строка курса месяца партнёра: закреплённая при закрытии, у открытого — действующая на конец месяца';

create or replace function report_amount(p_amount numeric, p_tenant uuid, p_period date,
                                         p_from text, p_to text)
returns numeric
language sql stable
as $$
    select case when p_from = p_to then p_amount
                else fx_convert(p_amount, report_rate_row(p_tenant, p_period, p_from, p_to), p_from)
           end
$$;

comment on function report_amount(numeric, uuid, date, text, text) is
    'Сумма в валюте консолидации по курсу месяца. null — курса нет, и отчёт обязан это сказать';
"""

LOOKUP_BACK = """
drop function if exists report_amount(numeric, uuid, date, text, text);
drop function if exists report_rate_row(uuid, date, text, text);
create or replace function fx_rate_on(p_base text, p_quote text, p_on date)
returns numeric
language sql stable
as $$
    select case
             when p_base = p_quote then 1::numeric
             else (
                 select rate from fx_rates
                  where base_currency = p_base and quote_currency = p_quote
                    and rate_date <= p_on
                  order by rate_date desc
                  limit 1
             )
           end
$$;
drop function if exists fx_convert(numeric, uuid, text);
drop function if exists fx_rate_row_on(text, text, date);
"""

# --- курс закрепляется за закрытым месяцем ------------------------------------
FIXING = """
alter table period_fx_rates enable row level security;
alter table period_fx_rates force  row level security;
create policy tenant_isolation on period_fx_rates
    for select
    using (tenant_id in (select app_tenant_ids()));

-- Пишут только триггеры ниже — функциями `security definer`, то есть владельцем
-- схемы. `force` распространяет политики и на него, поэтому владельцу нужна своя
-- политика: без неё закрытие месяца не записало бы ничего, а курс, введённый
-- администратором платформы, не дошёл бы до месяцев партнёров, где он не
-- состоит. Имя владельца берётся при накате: на управляемом Postgres оно своё.
-- Приложению — только чтение, своих партнёров.
do $$
begin
    execute format(
        'create policy maintained_by_owner on period_fx_rates for all to %I using (true) with check (true)',
        current_user
    );
end
$$;
revoke all on period_fx_rates from app_user;
grant select on period_fx_rates to app_user;

-- Закрепление читает партнёра и его месяцы владельцем — при накате миграции и
-- когда курс вводит администратор платформы, контекста партнёра нет, а `force`
-- закрыл бы владельцу обе таблицы: уже закрытые месяцы молча остались бы без
-- курса. Только чтение; факты закрепление не читает вовсе (см. ниже).
do $$
begin
    execute format('create policy owner_reads on tenants for select to %I using (true)', current_user);
    execute format('create policy owner_reads on periods for select to %I using (true)', current_user);
end
$$;

comment on table period_fx_rates is
    'Курс, закреплённый за закрытым месяцем партнёра: ссылка на неизменную строку справочника. Пишут только триггеры';
comment on column period_fx_rates.fx_rate_id is
    'Строка курса на конец месяца. Пусто — курса на день закрытия не было; заполняется один раз первым подходящим курсом';

create or replace function fix_period_rates(p_tenant uuid, p_period date)
returns int
language plpgsql security definer
set search_path = public, pg_temp
as $$
declare
    v_report text;
    v_end    date := (p_period + interval '1 month - 1 day')::date;
    v_count  int;
begin
    -- Закрепляется только закрытый месяц и только у своего партнёра; без
    -- контекста пользователя — обслуживание (накат миграции).
    if not exists (select 1 from periods
                    where tenant_id = p_tenant and period = p_period and status = 'closed') then
        raise exception 'курс закрепляется только за закрытым месяцем';
    end if;
    if app_user_id() is not null
       and p_tenant not in (select app_tenant_ids()) then
        raise exception 'месяц не найден';
    end if;

    select report_currency into v_report from tenants where id = p_tenant;

    insert into period_fx_rates (tenant_id, period, currency, report_currency, fx_rate_id)
    select p_tenant, p_period, c.currency, v_report, fx_rate_row_on(c.currency, v_report, v_end)
      -- Валюта учёта и все валюты, у которых есть курс к валюте консолидации.
      -- Факты не читаются намеренно: функция видела бы их глазами закрывающего,
      -- и валюта скрытой от него строки осталась бы без закрепления. Валюту без
      -- курса закрепит первый введённый курс (триггер ниже).
      from (select base_currency as currency from tenants where id = p_tenant
            union
            select case when r.base_currency = v_report then r.quote_currency
                        else r.base_currency end
              from fx_rates r
             where v_report in (r.base_currency, r.quote_currency)) c
     where c.currency <> v_report
    on conflict (tenant_id, period, currency, report_currency) do nothing;

    get diagnostics v_count = row_count;
    return v_count;
end
$$;

comment on function fix_period_rates(uuid, date) is
    'Закрепить за закрытым месяцем курсы его валют на конец месяца';

-- Зовёт её только триггер ниже (владельцем); приложению напрямую она не нужна.
revoke execute on function fix_period_rates(uuid, date) from public;
revoke execute on function fix_period_rates(uuid, date) from app_user;

create or replace function periods_fix_rates()
returns trigger
language plpgsql security definer
set search_path = public, pg_temp
as $$
begin
    if new.status = 'closed'
       and (tg_op = 'INSERT' or old.status is distinct from 'closed') then
        perform fix_period_rates(new.tenant_id, new.period);
    elsif tg_op = 'UPDATE' and old.status = 'closed' and new.status <> 'closed' then
        -- Открытие месяца — явное решение с причиной (D021): месяц снова
        -- считается по живому курсу и закрепит курс заново при закрытии.
        delete from period_fx_rates
         where tenant_id = new.tenant_id and period = new.period;
    end if;
    return null;
end
$$;

comment on function periods_fix_rates() is
    'Закрытие месяца закрепляет курсы, открытие снимает. Триггером: месяц закрывается не только приложением';

create trigger periods_fix_rates
    after insert or update of status on periods
    for each row execute function periods_fix_rates();

-- Закреплённая ссылка не меняется. Пустая заполняется один раз.
create or replace function period_fx_rates_guard()
returns trigger
language plpgsql
as $$
begin
    if old.fx_rate_id is not null
       or new.tenant_id <> old.tenant_id or new.period <> old.period
       or new.currency <> old.currency or new.report_currency <> old.report_currency then
        raise exception 'курс закрытого месяца % не меняется', to_char(old.period, 'YYYY-MM')
            using hint = 'чтобы пересчитать месяц по другому курсу, его открывают откатом расчёта';
    end if;
    return new;
end
$$;

create trigger period_fx_rates_guard
    before update on period_fx_rates
    for each row execute function period_fx_rates_guard();

-- Курса на день закрытия не было — первый подходящий введённый курс заполняет
-- пустую ссылку. Дальше она неизменна, как и заполненная при закрытии.
create or replace function fx_rates_complete_fixes()
returns trigger
language plpgsql security definer
set search_path = public, pg_temp
as $$
begin
    update period_fx_rates x
       set fx_rate_id = fx_rate_row_on(x.currency, x.report_currency,
                                       (x.period + interval '1 month - 1 day')::date),
           fixed_at = now()
     where x.fx_rate_id is null
       and ((x.currency = new.base_currency and x.report_currency = new.quote_currency)
         or (x.currency = new.quote_currency and x.report_currency = new.base_currency))
       and new.rate_date <= (x.period + interval '1 month - 1 day')::date;

    -- Валюта, курса к которой при закрытии не было вовсе: строки закрепления у
    -- месяца нет, и без этой вставки месяц считал бы её по живому курсу.
    insert into period_fx_rates (tenant_id, period, currency, report_currency, fx_rate_id)
    select p.tenant_id, p.period, c.currency, t.report_currency,
           fx_rate_row_on(c.currency, t.report_currency,
                          (p.period + interval '1 month - 1 day')::date)
      from periods p
      join tenants t on t.id = p.tenant_id
      cross join lateral (
          select case when t.report_currency = new.base_currency then new.quote_currency
                      else new.base_currency end as currency
      ) c
     where p.status = 'closed'
       and t.report_currency in (new.base_currency, new.quote_currency)
       and new.rate_date <= (p.period + interval '1 month - 1 day')::date
    on conflict (tenant_id, period, currency, report_currency) do nothing;
    return null;
end
$$;

create trigger fx_rates_complete_fixes
    after insert on fx_rates
    for each row execute function fx_rates_complete_fixes();

-- Уже закрытые месяцы закрепляют курс, действующий сейчас: другого у них нет.
select fix_period_rates(tenant_id, period) from periods where status = 'closed';
"""

FIXING_BACK = """
drop trigger if exists fx_rates_complete_fixes on fx_rates;
drop function if exists fx_rates_complete_fixes();
drop trigger if exists period_fx_rates_guard on period_fx_rates;
drop function if exists period_fx_rates_guard();
drop trigger if exists periods_fix_rates on periods;
drop function if exists periods_fix_rates();
drop function if exists fix_period_rates(uuid, date);
revoke all on period_fx_rates from app_user;
drop policy if exists owner_reads on periods;
drop policy if exists owner_reads on tenants;
drop policy if exists maintained_by_owner on period_fx_rates;
drop policy if exists tenant_isolation on period_fx_rates;
alter table period_fx_rates no force row level security;
alter table period_fx_rates disable row level security;
"""

# --- отчёты считают по тому же правилу ----------------------------------------
# `create or replace view` меняет выражение колонки без сноса зависимых
# представлений: имя и тип `amount_report` те же.
PNL_LINES = """
create or replace view pnl_lines with (security_invoker = true) as
select f.id           as fact_id,
       f.tenant_id,
       f.period,
       f.doc_date,
       f.unit_id,
       u.code         as unit_code,
       u.title        as unit_title,
       f.legal_entity_id,
       f.pnl_item_id,
       i.code         as pnl_code,
       i.title        as pnl_title,
       i.kind,
       f.ledger,
       f.counterparty_id,
       f.allocation,
       f.allocation_rule_id,
       f.source,
       f.channel,
       f.amount,
       f.currency,
       coalesce(
           f.amount_report,
           {amount_report}
       )              as amount_report,
       t.report_currency,
       f.document_id,
       f.title,
       f.expense_item_id,
       f.vat_rate,
       f.vat_amount,
       f.amount - coalesce(f.vat_amount, 0) as amount_net
  from facts f
  join tenants t   on t.id = f.tenant_id
  join pnl_items i on i.id = f.pnl_item_id
  left join units u on u.id = f.unit_id
 where f.superseded_at is null
   and f.allocation <> 'split';
"""

# Прежний пересчёт снимается: `fill_report_amounts` приколачивал сумму к факту
# мимо закреплённого курса, а `fx_rate_on` нужен был только ему и старому
# представлению. Откат возвращает их ровно в том виде, в каком их завёл `0230`.
DROP_OLD = """
drop function if exists fill_report_amounts(uuid, date);
drop function if exists fx_rate_on(text, text, date);
"""
RESTORE_OLD = import_module("core.migrations.0230_facts").FX

# Откат возвращает уникальность (курс, дата) без версий. Если курс уже
# исправляли, две строки одной даты её нарушат — и откат упал бы посреди
# операций с непонятной ошибкой. Поэтому проверка идёт первой и словами.
REVERSE_GUARD = """
do $$
begin
    if exists (select 1 from fx_rates
                group by base_currency, quote_currency, rate_date having count(*) > 1) then
        raise exception 'откат 0278 невозможен: есть исправленные курсы (две строки одной даты)'
            using hint = 'оставьте по одной строке на дату вручную, понимая, что закрытые месяцы ссылались на них';
    end if;
end
$$;
"""

VIEW = PNL_LINES.format(
    amount_report="report_amount(f.amount, f.tenant_id, f.period, f.currency, t.report_currency)",
)
VIEW_BACK = PNL_LINES.format(
    amount_report=(
        "round(f.amount * fx_rate_on(f.currency, t.report_currency,\n"
        "                                       (f.period + interval '1 month - 1 day')::date), 2)"
    ),
)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0277_two_hands'),
    ]

    operations = [
        migrations.CreateModel(
            name='PeriodFxRate',
            fields=[
                ('id', models.UUIDField(db_default=models.Func(function='gen_random_uuid'), primary_key=True, serialize=False)),
                ('period', models.DateField()),
                ('currency', models.TextField()),
                ('report_currency', models.TextField()),
                ('fixed_at', models.DateTimeField(db_default=models.Func(function='now'))),
            ],
            options={
                'db_table': 'period_fx_rates',
            },
        ),
        migrations.RemoveConstraint(
            model_name='fxrate',
            name='fx_rates_uniq',
        ),
        migrations.AddField(
            model_name='fxrate',
            name='entered_at',
            field=models.DateTimeField(db_default=models.Func(function='clock_timestamp')),
        ),
        migrations.AddField(
            model_name='fxrate',
            name='entered_by',
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='fxrate',
            name='note',
            field=models.TextField(db_default=''),
        ),
        migrations.AddConstraint(
            model_name='fxrate',
            constraint=models.UniqueConstraint(fields=('base_currency', 'quote_currency', 'rate_date', 'entered_at'), name='fx_rates_version_uniq'),
        ),
        migrations.AddConstraint(
            model_name='fxrate',
            constraint=models.CheckConstraint(condition=models.Q(('rate__gt', 0)), name='fx_rates_rate_positive'),
        ),
        migrations.AddConstraint(
            model_name='fxrate',
            constraint=models.CheckConstraint(condition=models.Q(('base_currency', models.F('quote_currency')), _negated=True), name='fx_rates_two_currencies'),
        ),
        migrations.AddField(
            model_name='periodfxrate',
            name='fx_rate',
            field=models.ForeignKey(blank=True, db_column='fx_rate_id', null=True, on_delete=django.db.models.deletion.PROTECT, to='core.fxrate'),
        ),
        migrations.AddField(
            model_name='periodfxrate',
            name='tenant',
            field=models.ForeignKey(db_column='tenant_id', on_delete=django.db.models.deletion.CASCADE, to='core.tenant'),
        ),
        migrations.AddConstraint(
            model_name='periodfxrate',
            constraint=models.UniqueConstraint(fields=('tenant', 'period', 'currency', 'report_currency'), name='period_fx_rates_once'),
        ),
        migrations.RunSQL(FX_RATES, FX_RATES_BACK),
        migrations.RunSQL(LOOKUP, LOOKUP_BACK),
        migrations.RunSQL(FIXING, FIXING_BACK),
        migrations.RunSQL(VIEW, VIEW_BACK),
        migrations.RunSQL(DROP_OLD, RESTORE_OLD),
        migrations.RunSQL(migrations.RunSQL.noop, REVERSE_GUARD),
    ]
