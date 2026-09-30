"""Привязка к точке делает человека своим для её управляющего (T221, D055).

`0020` завела правило «свой человек»: свой — тот, у кого есть версия условий
найма на видимой точке. Тогда точка у человека была ровно одна, и правило
совпадало с деньгами.

С T197 деньги разъехались с этим правилом. Затраты человека делятся между всеми
точками, к которым он привязан (`employee_units`): территориальный управляющий
на две пиццерии, офисный — на сеть. Привязка в правило «свой человек» не
входила вовсе, и получилась дыра ровно в одну сторону:

    часть ФОТ человека ложится в затраты точки, а её управляющий не находит
    этого человека в справочнике — ни имени, ни ставки.

Спросить, из-за кого вырос его P&L, управляющему не у кого. Обратное — что
привязка открывает лишнее — исключено тем, что условие то же самое
(`app_unit_is_visible`): виден человек, привязанный к **видимой** точке, а не
любой, у кого есть хоть одна привязка.

**Даты версий не проверяются** — по тому же доводу, что и в `0020`: право
видеть человека даёт факт работы у этой точки, а не то, что он работает там
сегодня. Иначе уволенный исчезал бы из закрытой ведомости, которая на него
ссылается, а «сегодня» в политике сделало бы видимость зависящей от момента
запроса: один и тот же закрытый месяц читался бы по-разному в июне и в июле.

**Условие написано явно, а не оставлено политике `employee_units`.** На той
таблице с `0267` стоит `unit_visibility`, то есть подзапрос и так вернул бы
только видимые строки. Но правило доступа, которое держится на политике
соседней таблицы, ломается молча при первой же её правке — а здесь оно читается
из кода.

Человек без условий найма и без привязок остаётся видимым только тому, у кого
ограничения по точкам нет вовсе. Это не меняется: `0020` разбирает, почему.
"""
from django.db import migrations

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

# Возврат — ровно текст `0020`, слово в слово: пересказ по памяти разошёлся бы
# с тем, что там написано, и откат оставил бы третье правило доступа.
BACK = """
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
$$;

comment on function app_employee_is_visible(uuid, uuid) is
    'Свой ли это человек: есть ли у него условия найма на видимой точке. Даты версий не учитываются';
"""


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0268_rule_frames'),
    ]

    operations = [migrations.RunSQL(FUNCTION, BACK)]
