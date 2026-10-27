"""Рабочий месяц партнёра — тот, на котором открываются списки денег и документов.

Раньше расходы и счета открывались на сегодняшнем месяце. Но работают обычно с
последним заведённым месяцем (июнь закрывают в июле), и экран, открывшийся на
пустом октябре, читался как «данных нет» (#310). Рабочий месяц — самый поздний
из заведённых у партнёра; месяцев нет вовсе — текущий календарный.

Выборка идёт под политиками базы, как любая другая: чужих месяцев она не видит.
"""
from __future__ import annotations

from datetime import date

from core.models import Period

from .principal import get_current_principal


def working_first(request) -> date:
    """Первое число рабочего месяца для вошедшего; без партнёра — текущий месяц."""
    who = get_current_principal(request)
    if who is not None and who.tenant_id is not None:
        latest = (
            Period.objects.filter(tenant_id=who.tenant_id)
            .order_by("-period").values_list("period", flat=True).first()
        )
        if latest is not None:
            return latest.replace(day=1)
    return date.today().replace(day=1)
