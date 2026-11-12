"""Расхождения сверки читает только тот, кто ведёт расчёт.

До этой миграции чтение `reconciliation_findings` ограничивалось одним
партнёром: писать и решать мог только `payrun.calculate`, а читать — любой
член пространства. Пока расхождения нигде не показывались списком, это ни на
что не влияло. С экраном «Ждут решения» (#332) строка расхождения — это человек
и две суммы по всем регистрам, поэтому граница стоит в базе, как и остальной
доступ продукта (D014), а не только в представлении.
"""
from django.db import migrations

SQL = """
create policy calculate_read on reconciliation_findings
    as restrictive for select
    using (app_has_permission(tenant_id, 'payrun.calculate'));
"""

REVERSE = "drop policy if exists calculate_read on reconciliation_findings;"


class Migration(migrations.Migration):
    dependencies = [("core", "0281_platform_account_protection")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
