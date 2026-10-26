"""Разбор первички в две руки: кто разнёс и кто сверил (T206, issue #174).

**Зачем.** Эталон (модуль 15, «Путь накладной»): операционный директор говорит,
чья накладная и за что, бухгалтер видит её уже разнесённой и сверяет сумму и
НДС, а в раскрытии строки P&L видно, кто разнёс и кто подтвердил. До этой
миграции разбор был одним действием одного человека, и второго имени взять было
неоткуда.

**Шаги — право формы роли, а не имя роли** (D060): «чья и за что» —
`suppliers.classify`, как и было; «сверка суммы и НДС» — новое
`suppliers.confirm`. Его получает каждая роль, у которой уже есть разбор: у
партнёра директор и бухгалтер равны (D036), и дать новое право одному из них
значило бы молча отменить то решение. Две руки держит не набор прав, а правило
этой таблицы: **сверяет не тот, кто разнёс, и не автор сверяемой версии.**

**Привязка.** Разнесение — к ключу строки (`fact_key`): разбор заменяет версию
факта, ключ остаётся (тот же приём у выписки, `0276`). Сверка — к версии
(`fact_id`): поменялась сумма — сверять заново, иначе подпись стояла бы под
тем, чего человек не видел.

**История только дописывается**, и держат это права таблицы: ни `update`, ни
`delete` у приложения нет. Автора и время ставит база умолчанием — права на эти
колонки у приложения тоже нет, вписать чужое имя нечем.

Сверка — отметка, а не ворота: несверенная строка остаётся в P&L (D076).
"""
from django.db import migrations

# Имя — в самой строке следа, снимком на момент шага, как `created_by_name` у
# правил. Читать его потом из `users` нельзя: там хэш пароля, и чужие учётки
# видит только тот, кто ведёт роли (`0243`). Своя строка видна каждому — её и
# читает функция в момент записи.
TABLE = """
create or replace function app_user_display_name()
returns text
language sql stable
set search_path = public
as $$
    select coalesce(nullif(full_name, ''), username)
      from users
     where id = app_user_id()
$$;

create table fact_reviews (
    id        uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id) on delete cascade,
    fact_id   uuid not null references facts (id) on delete cascade,
    fact_key  text not null,
    step      text not null check (step in ('allocated', 'confirmed')),
    done_by   uuid not null default app_user_id(),
    done_by_name text not null default coalesce(app_user_display_name(), ''),
    done_at   timestamptz not null default now(),
    unique (fact_id, step)
);
create index fact_reviews_key_idx on fact_reviews (tenant_id, fact_key, step);

comment on table fact_reviews is
    'Две руки разбора первички: кто разнёс (чья и за что) и кто сверил сумму и НДС. Только дописывается';
comment on column fact_reviews.fact_key is
    'Ключ строки (facts.dedup_key): разнесение переживает замену версии, сверка — нет';
comment on column fact_reviews.step is
    'allocated — разнесена (право suppliers.classify); confirmed — сверена (право suppliers.confirm)';
"""

DROP_TABLE = """
drop table if exists fact_reviews;
drop function if exists app_user_display_name();
"""

# Последнее разнесение строки — по ключу, а не по версии: разбор в закрытом
# месяце и правка счёта заменяют версию, а разнёсший остаётся тем же.
#
# Функцией, а не подзапросом в политике: политика `fact_reviews`, читающая
# саму `fact_reviews`, — это «infinite recursion detected in policy», даже когда
# читает она под другой командой. PL/pgSQL не встраивается в запрос, поэтому
# рекурсии нет; права вызывающего при этом те же (не `security definer`), то
# есть и чтение идёт под политиками.
FUNCTION = """
create or replace function app_last_allocator(p_tenant uuid, p_key text)
returns uuid
language plpgsql stable
set search_path = public
as $$
begin
    return (
        select r.done_by
          from fact_reviews r
         where r.tenant_id = p_tenant
           and r.fact_key = p_key
           and r.step = 'allocated'
         order by r.done_at desc, r.id desc
         limit 1
    );
end
$$;
comment on function app_last_allocator(uuid, text) is
    'Кто разнёс строку последним (по ключу строки). Сверяет не он — правило двух рук';
"""

DROP_FUNCTION = "drop function if exists app_last_allocator(uuid, text);"

LAST_ALLOCATOR = "app_last_allocator(fact_reviews.tenant_id, fact_reviews.fact_key)"

POLICIES = f"""
alter table fact_reviews enable row level security;
alter table fact_reviews force row level security;

create policy tenant_isolation on fact_reviews
    for all
    using (tenant_id in (select app_tenant_ids()))
    with check (tenant_id in (select app_tenant_ids()));

-- След строки виден тому, кому видна сама строка: чужая точка и скрытый
-- регистр отсекаются политиками `facts`, и второй ответ здесь не заводится.
create policy review_of_visible_line on fact_reviews
    as restrictive for select
    using (exists (select 1 from facts f where f.id = fact_reviews.fact_id));

create policy review_step on fact_reviews
    as restrictive for insert
    with check (
        done_by = app_user_id()
        -- действующая версия этой же строки этого же партнёра
        and exists (
            select 1 from facts f
             where f.id = fact_reviews.fact_id
               and f.tenant_id = fact_reviews.tenant_id
               and f.dedup_key = fact_reviews.fact_key
               and f.superseded_at is null
               -- автор действующей версии свою же версию не сверяет: новую
               -- версию с тем же ключом можно записать мимо разбора (правка
               -- счёта), и шага «разнесено» она не оставит
               and (fact_reviews.step <> 'confirmed'
                    or f.created_by is distinct from app_user_id())
        )
        and case step
            when 'allocated' then app_has_permission(tenant_id, 'suppliers.classify')
            when 'confirmed' then
                app_has_permission(tenant_id, 'suppliers.confirm')
                -- сверять нечего, пока никто не сказал, чья строка и за что,
                -- и сверяет не тот, кто разнёс
                and {LAST_ALLOCATOR} is not null
                and {LAST_ALLOCATOR} <> app_user_id()
            else false
        end
    );
"""

DROP_POLICIES = """
drop policy if exists review_step on fact_reviews;
drop policy if exists review_of_visible_line on fact_reviews;
drop policy if exists tenant_isolation on fact_reviews;
"""

# `revoke` обязателен: умолчания схемы (`0005`) выдают приложению всё на любую
# новую таблицу. Вставка — только по колонкам строки и шага: автор, его имя и
# время — умолчания базы.
PRIVILEGES = """
revoke all on fact_reviews from app_user;
grant select on fact_reviews to app_user;
grant insert (tenant_id, fact_id, fact_key, step) on fact_reviews to app_user;
"""

# Никто ничего не теряет и никто не получает лишнего: право сверки — тем же
# ролям, у которых есть разбор. Снимок формы (`shipped_shape`) правится вместе с
# набором тем же доводом, что в `0274`: иначе доставка форм приняла бы роль за
# правленную партнёром и перестала бы её вести.
NEW = '\'["suppliers.confirm"]\'::jsonb'

GRANT = f"""
update roles
   set permissions = permissions || {NEW}
 where permissions ? 'suppliers.classify'
   and not permissions ? 'suppliers.confirm';

update roles
   set shipped_shape = jsonb_set(
           shipped_shape, '{{permissions}}',
           (shipped_shape -> 'permissions') || {NEW})
 where jsonb_typeof(shipped_shape -> 'permissions') = 'array'
   and (shipped_shape -> 'permissions') ? 'suppliers.classify'
   and not ((shipped_shape -> 'permissions') ? 'suppliers.confirm');

do $$
declare
    stuck int;
begin
    select count(*) into stuck
      from roles
     where permissions ? 'suppliers.classify'
       and not permissions ? 'suppliers.confirm';
    if stuck > 0 then
        raise exception
            'право сверки не доехало до % ролей: строки отрезаны политиками', stuck;
    end if;
end $$;
"""

REVOKE = """
update roles set permissions = permissions - 'suppliers.confirm';
update roles
   set shipped_shape = jsonb_set(
           shipped_shape, '{permissions}',
           (shipped_shape -> 'permissions') - 'suppliers.confirm')
 where jsonb_typeof(shipped_shape -> 'permissions') = 'array';
"""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0276_bank_statement"),
    ]

    operations = [
        migrations.RunSQL(TABLE, DROP_TABLE),
        migrations.RunSQL(FUNCTION, DROP_FUNCTION),
        migrations.RunSQL(POLICIES, DROP_POLICIES),
        migrations.RunSQL(PRIVILEGES, "revoke all on fact_reviews from app_user;"),
        migrations.RunSQL(GRANT, REVOKE),
    ]
