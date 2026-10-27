"""Вход через Google: номер аккаунта у учётки и окно на одну строку (T241, D098).

Google подтверждает ровно одно — человек владеет почтой. Кого пускать, решает
не он, а заведённая учётка: незнакомая почта получает отказ, учётка не
заводится (самостоятельной регистрации в продукте нет).

**Колонка `google_sub`.** Постоянный номер аккаунта Google. Почта у аккаунта
может смениться или достаться другому человеку, номер — нет. Первый вход по
почте записывает его за учёткой, следующие ищут сначала по нему. Уникален:
один аккаунт Google — одна учётка.

**Политика `users_login_google`.** Пока человек не вошёл, таблица `users` не
отдаёт ни строки (`0010`). Вход паролем открывает одну строку по логину
(`users_login_attempt`); вход через Google — по образцу той же политики, но по
номеру аккаунта (`app.login_google_sub`) или по почте без учёта регистра
(`app.login_email`). Обе настройки выставляет только код входа
(`web/google_login.py`), на одну транзакцию, и снимает сразу после поиска.
Пустая почта не совпадает ни с чем: у учёток без почты окна нет. Что почта
подтверждена в Google, проверяет код до того, как выставить настройку, —
политика этого знать не может.

RLS отсекает строки, а не колонки: в открытой строке видны все колонки, как и
у `users_login_attempt`. Код поиска читает только `id`, `email`, `google_sub`,
`is_active`.
"""
from django.db import migrations, models

FORWARD = """
comment on column users.google_sub is
    'Постоянный номер аккаунта Google (sub), которым человек входит. Записывается '
    'при первом входе по почте или привязкой со страницы учётной записи.';

create policy users_login_google on users
    for select using (
        (
            nullif(current_setting('app.login_google_sub', true), '') is not null
            and google_sub = current_setting('app.login_google_sub', true)
        )
        or (
            nullif(current_setting('app.login_email', true), '') is not null
            and email <> ''
            and lower(email) = lower(current_setting('app.login_email', true))
        )
    );
"""

BACKWARD = "drop policy if exists users_login_google on users;"


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0278_consolidation_rates'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='google_sub',
            field=models.TextField(blank=True, null=True, unique=True),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
