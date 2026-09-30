"""Люди и условия найма — свои права, а не общее «ведение справочников» (T236).

**Что было.** Одно право `directory.manage` закрывало всё сразу: точки, юрлица,
статьи, контрагентов, кассы, группы — и заодно сотрудников, их точки, условия
найма, надбавки и должности. Эталон «Роли и права» разводит это на три строки:
«Заводить и увольнять людей», «Менять ставки и условия найма», «Точки, юрлица,
статьи расходов» — и даёт их разным ролям. Одним правом такое не настроить:
партнёр, которому нужен бухгалтер, меняющий ставки, но не заводящий точки, не
мог этого сделать вовсе (D060 просил именно такой гибкости).

**Что стало.**

| право | таблицы |
|---|---|
| `staff.manage` | `employees`, `employee_units` |
| `terms.manage` | `employment_terms`, `employee_allowances`, `positions` |
| `directory.manage` | всё остальное, как прежде |

**Никто ничего не теряет.** Каждая роль, у которой было `directory.manage`,
получает оба новых права — и в наборе, и в снимке формы (`shipped_shape`).
Снимок правится вместе с набором намеренно: доставка форм
(`core/role_delivery.py`) сравнивает базу со снимком, и роль, у которой набор
вырос, а снимок нет, она приняла бы за правленную партнёром и перестала бы
вести. Добавлением, а не переприсваиванием: у партнёра набор мог быть правлен
экраном ролей, и запись списком стёрла бы его правку.

**Проверка в конце** — тем же приёмом, что в `0230`: `update` под политиками
меняет ноль строк молча, поэтому проверяется результат, а не оператор.
"""
from django.db import migrations

STAFF = ("employees", "employee_units")
TERMS = ("employment_terms", "employee_allowances", "positions")

# Имена прежних политик — как их завели свои миграции (0130, 0249, 0258, 0262).
OLD_NAMES = {
    "employees": ("directory_manage_insert", "directory_manage_update", "directory_manage_delete"),
    "employment_terms": (
        "directory_manage_insert", "directory_manage_update", "directory_manage_delete",
    ),
    "positions": ("directory_manage_insert", "directory_manage_update", "directory_manage_delete"),
    "employee_allowances": ("directory_write", "directory_update", "directory_delete"),
    "employee_units": ("directory_write", "directory_update", "directory_delete"),
}


def _policies(table: str, names: tuple[str, str, str], code: str) -> str:
    insert, update, delete = names
    check = f"app_has_permission(tenant_id, '{code}')"
    return f"""
create policy {insert} on {table} as restrictive for insert with check ({check});
create policy {update} on {table} as restrictive for update with check ({check});
create policy {delete} on {table} as restrictive for delete using ({check});
"""


def _drop(table: str, names) -> str:
    return "".join(f"drop policy if exists {name} on {table};\n" for name in names)


def _new_names(code: str) -> tuple[str, str, str]:
    stem = code.replace(".", "_")
    return (f"{stem}_insert", f"{stem}_update", f"{stem}_delete")


def _forward() -> str:
    parts = []
    for tables, code in ((STAFF, "staff.manage"), (TERMS, "terms.manage")):
        for table in tables:
            parts.append(_drop(table, OLD_NAMES[table]))
            parts.append(_policies(table, _new_names(code), code))
    return "".join(parts)


def _backward() -> str:
    parts = []
    for tables, code in ((STAFF, "staff.manage"), (TERMS, "terms.manage")):
        for table in tables:
            parts.append(_drop(table, _new_names(code)))
            parts.append(_policies(table, OLD_NAMES[table], "directory.manage"))
    return "".join(parts)


NEW = '\'["staff.manage", "terms.manage"]\'::jsonb'

GRANT = f"""
update roles
   set permissions = permissions || {NEW}
 where permissions ? 'directory.manage'
   and not (permissions ? 'staff.manage' and permissions ? 'terms.manage');

update roles
   set shipped_shape = jsonb_set(
           shipped_shape, '{{permissions}}',
           (shipped_shape -> 'permissions') || {NEW})
 where jsonb_typeof(shipped_shape -> 'permissions') = 'array'
   and (shipped_shape -> 'permissions') ? 'directory.manage'
   and not ((shipped_shape -> 'permissions') ? 'staff.manage');

do $$
declare
    stuck int;
begin
    select count(*) into stuck
      from roles
     where permissions ? 'directory.manage'
       and not (permissions ? 'staff.manage' and permissions ? 'terms.manage');
    if stuck > 0 then
        raise exception
            'права на людей и условия найма не доехали до % ролей: '
            'строки отрезаны политиками', stuck;
    end if;
end $$;
"""

REVOKE = """
update roles set permissions = permissions - 'staff.manage' - 'terms.manage';
update roles
   set shipped_shape = jsonb_set(
           shipped_shape, '{permissions}',
           (shipped_shape -> 'permissions') - 'staff.manage' - 'terms.manage')
 where jsonb_typeof(shipped_shape -> 'permissions') = 'array';
"""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0273_employee_units_share_positive"),
    ]

    operations = [
        migrations.RunSQL(GRANT, REVOKE),
        migrations.RunSQL(_forward(), _backward()),
    ]
