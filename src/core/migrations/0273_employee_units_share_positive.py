"""Доля точки в наборе человека — положительная или пустая (T221).

Расчёт делит ФОТ человека по сумме весов его точек (`payrun.posting._shares`).
Все доли нулевые — делить не на что. Форма такие доли отвергает словами, база
теперь тоже: запись мимо формы не должна доживать до дня утверждения месяца.

Если в базе уже есть строки с нулевой или отрицательной долей, миграция упадёт
— нарочно: какую долю они имели в виду, знает партнёр, а не миграция.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0272_shared_rules_write_narrowed'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='employeeunit',
            constraint=models.CheckConstraint(condition=models.Q(('share__isnull', True), ('share__gt', 0), _connector='OR'), name='employee_units_share_positive'),
        ),
    ]
