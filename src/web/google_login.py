"""Кого пускать по аккаунту Google и как привязать аккаунт к учётке (T241, D098).

Google подтверждает ровно одно — человек владеет почтой (`web/google_auth.py`).
Кого пускать, решает заведённая учётка MAXIMUS: самостоятельной регистрации нет,
незнакомая почта получает отказ словами.

Порядок поиска — как в DECIMUS (аудит 12.10, F-027): сначала по постоянному
номеру аккаунта (`users.google_sub`), потом по подтверждённой почте. Почта у
аккаунта может смениться или достаться другому человеку, номер — нет. Нашлась
учётка по почте — номер записывается за ней, и дальше человек входит по нему;
у учётки уже записан ДРУГОЙ номер — отказ: ту же почту принёс чужой аккаунт.

Администратор платформы (`platform_admins`) входит через Google, только если
аккаунт из домена компании по заявке `hd` (DECIMUS D430, F-028): совпадение
почты с его учёткой без домена компании — отказ. Людей партнёров это не
касается: у них любая почта, их пускает совпадение с заведённой учёткой.

**Окно в таблицу учёток.** Пока никто не вошёл, `users` не отдаёт ни строки.
Поиск выставляет `app.login_google_sub` и `app.login_email` на одну транзакцию,
и политика `users_login_google` (`0279`) открывает ровно совпавшие строки; окно
закрывается сразу после поиска, а не в конце запроса. Читаются только колонки,
нужные для решения.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from django.contrib import auth as django_auth
from django.db import IntegrityError, connection, transaction
from django.utils.translation import gettext_lazy as _

from .auth import BACKEND
from .dbcontext import set_db_context
from .google_auth import GoogleIdentity, hq_domain_ok

log = logging.getLogger(__name__)

SUB_SETTING = "app.login_google_sub"
EMAIL_SETTING = "app.login_email"

# Отказы — словами, каждый говорит, что делать дальше. Ключ уходит в адрес
# страницы входа (`?google=…`), а текст берётся отсюда: текст в адресе можно
# было бы подменить ссылкой.
REFUSALS = {
    "unknown": _(
        "Этой почты нет среди учётных записей MAXIMUS. Войдите логином и паролем "
        "или попросите администратора вашего партнёра указать эту почту в вашей "
        "учётной записи."
    ),
    "ambiguous": _(
        "Эта почта указана сразу в нескольких учётных записях, и понять, в какую "
        "входить, нельзя. Войдите логином и паролем и сообщите администратору."
    ),
    "other_google": _(
        "Ваша учётная запись уже привязана к другому аккаунту Google. Войдите тем "
        "аккаунтом или логином и паролем."
    ),
    "hq_domain": _(
        "Администратор платформы входит через Google только рабочим аккаунтом "
        "компании. Войдите рабочим аккаунтом или логином и паролем."
    ),
    "failed": _(
        "Войти через Google не получилось. Попробуйте ещё раз или войдите логином "
        "и паролем."
    ),
    "taken": _(
        "Этот аккаунт Google уже привязан к другой учётной записи MAXIMUS. "
        "Привяжите другой аккаунт или попросите администратора разобраться."
    ),
}


class GoogleRefusal(Exception):
    """Не пустили. `code` — ключ `REFUSALS`, человеку показывается его текст."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Found:
    """Что поиск узнал об учётке. Ровно то, что нужно для решения."""

    user_id: UUID
    google_sub: str | None
    by_subject: bool


def _set_scope(subject: str, email: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "select set_config(%s, %s, true), set_config(%s, %s, true)",
            (SUB_SETTING, subject, EMAIL_SETTING, email),
        )


def find_account(identity: GoogleIdentity) -> Found:
    """Учётка этого аккаунта Google: по номеру, иначе по почте. Иначе — отказ."""
    from core.models import User

    _set_scope(identity.subject, identity.email)
    try:
        by_sub = list(
            User.objects.filter(google_sub=identity.subject, is_active=True)
            .values_list("id", "google_sub")[:2]
        )
        by_mail = [] if by_sub else list(
            User.objects.filter(email__iexact=identity.email, is_active=True)
            .exclude(email="")
            .values_list("id", "google_sub")[:2]
        )
    finally:
        # Окно закрывается сразу же, а не «до конца запроса».
        _set_scope("", "")
    if by_sub:
        return Found(by_sub[0][0], by_sub[0][1], by_subject=True)
    if not by_mail:
        raise GoogleRefusal("unknown")
    if len(by_mail) > 1:
        raise GoogleRefusal("ambiguous")
    user_id, stored = by_mail[0]
    if stored and stored != identity.subject:
        raise GoogleRefusal("other_google")
    return Found(user_id, stored, by_subject=False)


def _is_platform_admin(user_id: UUID) -> bool:
    from core.spaces import is_platform_admin

    return is_platform_admin(user_id)


def login_with_google(request, identity: GoogleIdentity, hq_domains: frozenset[str]):
    """Войти по проверенному аккаунту Google. Не пустили — `GoogleRefusal`."""
    from core.models import User

    found = find_account(identity)
    # Дальше — от имени найденного: свою строку и своё членство в
    # `platform_admins` он видит своей политикой, без обхода разграничения.
    set_db_context(connection, found.user_id)
    if _is_platform_admin(found.user_id) and not hq_domain_ok(identity, hq_domains):
        # Подсказка — только в журнал: человеку не нужен список доменов, а тому,
        # кто ведёт площадку, нужно знать, что дописать.
        log.warning(
            "google login refused: platform admin account outside company domains "
            "(hd=%r). If this domain is the company's, add it to GOOGLE_HQ_DOMAINS.",
            identity.hosted_domain or "",
        )
        set_db_context(connection, None)
        raise GoogleRefusal("hq_domain")
    if not found.by_subject:
        _bind(found.user_id, identity.subject)
    user = User.objects.get(pk=found.user_id)
    django_auth.login(request, user, backend=BACKEND)
    return user


def _bind(user_id: UUID, subject: str) -> None:
    """Записать номер аккаунта за своей учёткой (под своим контекстом)."""
    from core.models import User

    try:
        with transaction.atomic():
            User.objects.filter(pk=user_id).update(google_sub=subject)
    except IntegrityError as clash:
        # Номер уже за другой учёткой — значит, по номеру мы её не нашли только
        # потому, что она отключена. Пускать в эту — значило бы делить аккаунт.
        raise GoogleRefusal("taken") from clash


def link_google(user_id: UUID, identity: GoogleIdentity) -> None:
    """Привязать аккаунт Google к своей учётке со страницы учётной записи."""
    from core.models import User

    with connection.cursor() as cursor:
        cursor.execute("select set_config(%s, %s, true)", (SUB_SETTING, identity.subject))
    try:
        owners = list(
            User.objects.filter(google_sub=identity.subject).values_list("id", flat=True)[:2]
        )
    finally:
        with connection.cursor() as cursor:
            cursor.execute("select set_config(%s, '', true)", (SUB_SETTING,))
    if any(owner != user_id for owner in owners):
        raise GoogleRefusal("taken")
    _bind(user_id, identity.subject)


def unlink_google(user_id: UUID) -> None:
    """Отвязать аккаунт Google от своей учётки: дальше вход только паролем."""
    from core.models import User

    User.objects.filter(pk=user_id).update(google_sub=None)
