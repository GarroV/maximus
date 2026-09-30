"""Экран ролей: кто что вправе делать и у кого какая роль (T171, issue #77).

Зачем понадобился. Право `roles.manage` полгода лежало в ролях и в миграциях
без единого потребителя, и это было не косметикой: разграничение доступа
держалось на доводе «администратор в любой момент вправе выписать себе
недостающее право». Выписать было нечем. Владелец, вошедший администратором,
уткнулся в «Расчёт периода не входит в права вашей роли. Попросите того, у кого
это право есть» — и попросить оказалось некого.

Что здесь есть.

**Права роли галочками.** Список прав — из `web/permissions.py`, то есть ровно
тот, что стоит в политиках базы. Второй список в разметке разъехался бы с
первым, и экран обещал бы то, чего база не даст.

**Вторая роль человеку.** Ради этого всё и затевалось (D047): у партнёра
бухгалтер часто ведёт весь проект и должен быть ещё и администратором, а там,
где эти люди разные, разделение обязанностей остаётся.

**Одна страница — одна функция** (T225, D081, D087). До 30.09.2026 всё это
стояло одним экраном `/roles/`: матрица прав, у каждого человека в строке своя
форма выдачи с двумя выпадающими списками, форма приглашения и история. На 390
строка человека не помещалась вовсе, а вопрос «что позволяет роль» и вопрос «у
кого какая роль» читались как один. Теперь раздел — четыре страницы и ряд
ссылок между ними (`_sections`):

| адрес | что на ней |
|---|---|
| `/roles/` | права ролей: что позволяет каждая |
| `/roles/people/` | люди партнёра и их роли; человек — ссылка на свою страницу |
| `/roles/people/<id>/` | роли одного человека: выдать, снять, срок, причина |
| `/roles/invite/` | пригласить человека |
| `/roles/history/` | история доступов |

Отказ формы показывается на той же странице, где форма, и с тем, что человек
уже набрал: прежде любая ошибка возвращала весь экран целиком, и причина
отказа оказывалась над таблицей прав, далеко от поля, которое надо поправить.

Чего здесь нет и почему.

**Заведения ролей и учёток.** Роль — это набор прав, а не человек; новые роли
появляются пресетом страны. Экран правит то, что есть, и раздаёт это людям.
Заводить учётки отсюда тем более нельзя: это отдельный разговор про пароли.

**Своей проверки прав.** Право спрашивается один раз (`permissions.check`), а
гарантия стоит в базе (`0242`): роль без `roles.manage` не запишет строку, даже
если этот модуль однажды забудет спросить. Тихого отказа при этом нет —
представление смотрит, изменилась ли строка на самом деле, и говорит словами.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, connection, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core.models import AccessLogEntry, Membership, Role, Unit, User
from core.roles import ALL_PERMISSIONS, NEVER, OPTIONAL, leads_one_unit

from . import permissions
from .principal import get_current_principal
from .roles_display import rights_matrix, role_choice, shown_role_title
from .roles_effects import role_effects

# Права, которые можно выдать с экрана, — ВСЕ права продукта, а не их список
# рядом (T203). Список здесь был своей копией, и она успела разъехаться: право
# `suppliers.classify` появилось с разбором первички, а на экране ролей его не
# было вовсе — то есть выдать или снять его было нечем ни одним способом.
GRANTABLE = ALL_PERMISSIONS


def _title(role) -> str:
    """Название роли на языке страницы (`web.roles_display`)."""
    return shown_role_title(role.title, role.code)


def _state_of(role, code: str) -> str:
    """Состояние клетки «роль × право» по матрице роли (T203, D060).

    Клетки нет — считаем `optional`. То же умолчание, что и в базе: пустая
    матрица означает «стен не заведено», а не «нельзя ничего», иначе миграция
    молча обесправила бы каждую роль, заведённую до её появления.
    """
    return (role.permission_states or {}).get(code, OPTIONAL)


def _people(tenant_id, user_id=None):
    """Люди партнёра с их ролями — одним запросом, а не по строке на человека.

    `user_id` — один человек, для его страницы. Отдельной выборки для неё нет
    намеренно: имя, почта и срок роли на двух страницах обязаны читаться
    одинаково, а два запроса разошлись бы при первой же правке одного из них.

    Имена приходят из `users`, которую `0243` открыла тому, кто ведёт роли.
    Учётка без имени показывается логином: пусто в этом столбце означало бы,
    что человека нет, — а он есть.

    Срок роли едет рядом с самой ролью (T188): «Администратор сети до 31.07» —
    это одна вещь, и разносить её по разным столбцам значило бы предложить
    человеку самому сопоставлять две колонки.
    """
    with connection.cursor() as cur:
        cur.execute(
            """
            select m.user_id,
                   coalesce(nullif(u.full_name, ''), u.username, m.user_id::text) as who,
                   coalesce(u.email, '')                     as mail,
                   array_agg(r.id::text order by r.title)    as role_ids,
                   array_agg(r.title order by r.title)       as role_titles,
                   array_agg(r.code order by r.title)        as role_codes,
                   array_agg(coalesce(to_char(m.expires_at, 'DD.MM.YYYY'), '')
                             order by r.title)               as role_until,
                   bool_or(m.unit_ids is null)               as all_units,
                   (select array_agg(distinct un.title)
                      from memberships m2
                      join units un on un.id = any(m2.unit_ids)
                     where m2.tenant_id = m.tenant_id
                       and m2.user_id = m.user_id)           as unit_titles,
                   u.last_login                              as seen
              from memberships m
              join roles r on r.id = m.role_id
              left join users u on u.id = m.user_id
             where m.tenant_id = %s
               and (%s::uuid is null or m.user_id = %s::uuid)
             group by m.tenant_id, m.user_id, who, mail, u.last_login
             order by who
            """,
            [tenant_id, user_id, user_id],
        )
        return [
            {
                "user_id": row[0],
                "who": row[1],
                "mail": row[2],
                "roles": [
                    {"id": rid, "code": code, "title": shown_role_title(title, code),
                     "until": until}
                    for rid, title, code, until in zip(row[3], row[4], row[5], row[6], strict=True)
                ],
                # Точки — объединение по всем ролям человека: роль без точек
                # (`unit_ids is null`) означает все точки партнёра, и тогда
                # перечислять отдельные бессмысленно (D047 — роли складываются).
                "all_units": row[7],
                "units": sorted(row[8] or []),
                "seen": row[9],
            }
            for row in cur.fetchall()
        ]


# Сколько записей истории показывать сразу. История не удаляется никогда, и
# через год её тут тысячи; экран отвечает на вопрос «что происходило недавно»,
# а разбор давнего — отдельный разговор и отдельная страница.
HISTORY_SHOWN = 50


def _history(tenant_id):
    """Кто, кому, когда и зачем открывал доступ.

    Имена берутся соединением с `users` — той же выборкой, что и список людей.
    Названия ролей — снимком из самой записи, а не по ссылке: роль переименуют,
    и «выдал роль администратора» превратилось бы в «выдал роль».
    """
    with connection.cursor() as cur:
        cur.execute(
            """
            select to_char(l.at, 'DD.MM.YYYY'),
                   coalesce(nullif(a.full_name, ''), a.username, '—'),
                   coalesce(nullif(s.full_name, ''), s.username, '—'),
                   l.action, l.role_title,
                   coalesce(to_char(l.until, 'DD.MM.YYYY'), ''),
                   l.reason
              from access_log l
              left join users a on a.id = l.actor_user_id
              left join users s on s.id = l.subject_user_id
             where l.tenant_id = %s
             order by l.at desc
             limit %s
            """,
            [tenant_id, HISTORY_SHOWN],
        )
        return [
            {
                "at": row[0], "actor": row[1], "subject": row[2],
                "action": row[3], "role": shown_role_title(row[4]),
                "until": row[5], "reason": row[6],
            }
            for row in cur.fetchall()
        ]



def membership_units(chosen: str, tenant_id, role):
    """Точки членства и отказ словами, если выбор не сходится с ролью.

    Возвращает `(unit_ids, отказ)`. `unit_ids is None` — все точки партнёра, и
    это законно ровно для тех ролей, которые его целиком и ведут
    (`core.roles.leads_one_unit`). Общая для страницы партнёра и платформы:
    правило, написанное дважды, однажды разошлось бы, и платформа выдавала бы
    управляющему все точки, как это и было до разбора прав T225.

    Молчаливого умолчания здесь нет намеренно: и «забыл выбрать точку», и
    «выбрал точку роли, которая ведёт всё» — это расхождение между тем, что
    человек имел в виду, и тем, что получит. Оба случая называются словами, а
    не разрешаются за него в ту или другую сторону.
    """
    chosen = (chosen or "").strip()

    if not leads_one_unit(role.code):
        if chosen:
            return None, _(
                "Роль «%(role)s» ведёт всего партнёра — точка для неё не выбирается."
            ) % {"role": _title(role)}
        return None, ""

    if not chosen:
        return None, _(
            "Роль «%(role)s» ведёт одну точку — выберите её. "
            "Без точки человек получил бы все точки партнёра."
        ) % {"role": _title(role)}

    try:
        unit = Unit.objects.filter(pk=chosen, tenant_id=tenant_id).first()
    except (DjangoValidationError, ValueError):
        unit = None
    if unit is None:
        # Теми же словами, что у чужой строки: по ответу нельзя понять, есть ли
        # такая точка у другого партнёра (D023).
        return None, _("Такой точки у этого партнёра нет.")
    return [unit.pk], ""


def _membership_units(request, who, role):
    return membership_units(request.POST.get("unit"), who.tenant_id, role)


# Ряд ссылок раздела (D081): страницы раздела — шаги внутри одного пункта левой
# панели, а не пункты панели сами по себе. Панель ведёт в «Роли и права», а здесь
# человек выбирает, что именно делает. Ссылки, а не переключатель на странице:
# у каждой функции свой адрес, который можно отправить коллеге.
SECTIONS = (
    ("roles", gettext_lazy("Права ролей")),
    ("roles-people", gettext_lazy("Люди и их роли")),
    ("roles-invite", gettext_lazy("Пригласить человека")),
    ("roles-history", gettext_lazy("История доступов")),
)


def _sections(current: str) -> list:
    # Страница одного человека — шаг внутри «Людей», а не пятая страница
    # раздела: ряда у неё нет, есть дорога назад. Ряд, где ни один пункт не
    # выделен, читался бы как второй набор навигации (сверка norma).
    if not current:
        return []
    return [
        {"title": title, "url": reverse(route), "selected": route == current}
        for route, title in SECTIONS
    ]


def _show(request, template: str, current: str, context: dict, *,
          error: str = "", status: int = 200):
    """Страница раздела: ряд ссылок, сообщение об успехе, отказ словами.

    Сообщение об успехе приходит через сессию: после записи — перенаправление
    (обновление страницы не повторяет записи), и сказать «сохранено» надо уже на
    той странице, куда человек попал.
    """
    return render(
        request,
        template,
        {
            **context,
            "sections": _sections(current),
            "notice": request.session.pop("roles_notice", ""),
            "error": error,
        },
        status=status,
    )


def _guard(request):
    """Кто смотрит и отказ словами, если вести роли ему не положено.

    Одна развилка на все страницы раздела: пять страниц с пятью своими
    проверками однажды разошлись бы, и одна из них открылась бы без права.
    """
    who = get_current_principal(request)
    try:
        permissions.check(who, permissions.ROLES_MANAGE)
    except permissions.PermissionRefused as refusal:
        return who, _refusal_page(request, refusal)
    return who, None


def _choices(who) -> dict:
    """Роли и точки партнёра — то, из чего выбирают в формах выдачи."""
    return {
        "roles": [
            role_choice(role)
            for role in Role.objects.filter(tenant_id=who.tenant_id).order_by("title")
        ],
        "units": list(Unit.objects.filter(tenant_id=who.tenant_id).order_by("code")),
        "today": date.today().isoformat(),
    }


def _rights_page(request, who, *, error: str = "", status: int = 200):
    roles = list(Role.objects.filter(tenant_id=who.tenant_id).order_by("title"))
    return _show(request, "web/roles/index.html", "roles", rights_matrix(roles),
                 error=error, status=status)


def _people_page(request, who, *, error: str = "", status: int = 200):
    return _show(request, "web/roles/people.html", "roles-people",
                 {"people": _people(who.tenant_id)}, error=error, status=status)


def _person_page(request, who, person, *, error: str = "", status: int = 200):
    roles = list(Role.objects.filter(tenant_id=who.tenant_id).order_by("title"))
    effects = role_effects([str(held["id"]) for held in person["roles"]], roles)
    shown = {
        **person,
        "roles": [
            {**held, "loses": effects["loses"].get(str(held["id"]), [])}
            for held in person["roles"]
        ],
    }
    return _show(request, "web/roles/person.html", "",
                 {"person": shown, "now": effects["now"], "offers": effects["offers"],
                  **_choices(who)}, error=error, status=status)


def _invite_page(request, who, *, error: str = "", status: int = 200):
    # Что человек уже набрал, возвращается в форму: отказ из-за одного поля не
    # должен стоить ему повторного ввода остальных пяти.
    filled = {
        key: (request.POST.get(key) or "").strip()
        for key in ("full_name", "email", "role", "unit", "until", "reason")
    } if request.method == "POST" else {}
    return _show(request, "web/roles/invite.html", "roles-invite",
                 {"filled": filled, **_choices(who)}, error=error, status=status)


@login_required
def index(request):
    """Права ролей. Кому не положено — отказ словами, а не пустой экран."""
    who, refused = _guard(request)
    if refused:
        return refused
    return _rights_page(request, who)


@login_required
def people(request):
    """Люди партнёра и их роли; у каждого — ссылка на его страницу."""
    who, refused = _guard(request)
    if refused:
        return refused
    return _people_page(request, who)


@login_required
def history(request):
    """Кто, кому, когда и зачем открывал и закрывал доступ."""
    who, refused = _guard(request)
    if refused:
        return refused
    return _show(request, "web/roles/history.html", "roles-history",
                 {"history": _history(who.tenant_id), "shown": HISTORY_SHOWN})


@login_required
def role_rights(request, role_id):
    """Сохранить права роли. Только POST: это запись, а не просмотр.

    Прежде метод не проверялся, и обычный переход по этому адресу сохранял
    роль с пустым списком прав — то есть ссылка, открытая администратором,
    молча снимала с роли всё. Теперь чтение отсюда уводит на страницу прав.
    """
    who, refused = _guard(request)
    if refused:
        return refused
    if request.method != "POST":
        return redirect(reverse("roles"))

    role = Role.objects.filter(pk=role_id, tenant_id=who.tenant_id).first()
    if role is None:
        # Роль чужого партнёра или общая: политика её всё равно не отдаст на
        # запись, но человеку надо сказать словами, а не кодом ошибки.
        return _rights_page(
            request, who,
            error=_("Такой роли у этого партнёра нет."),
            status=404,
        )

    chosen = [code for code in GRANTABLE if request.POST.get(f"right:{code}") == "on"]
    # Стена проверяется ДО записи и называется словами. Молча выкинуть такое
    # право из списка — худший из исходов: администратор уверен, что выдал его,
    # а его нет. Гарантия при этом всё равно в базе (`check` из `0263`): она
    # держится и на владельце таблиц, и на экране, который забудет спросить.
    walled = [code for code in chosen if _state_of(role, code) == NEVER]
    if walled:
        return _rights_page(
            request, who,
            error=_("Права роли не изменены: «%(right)s» у этой роли не бывает.")
            % {"right": permissions.title(walled[0])},
            status=409,
        )
    changed = Role.objects.filter(pk=role.pk, tenant_id=who.tenant_id).update(permissions=chosen)
    if not changed:
        # Политика отказала молча — «изменено 0 строк». Такое молчание и есть
        # худший исход: человек уверен, что право выдано.
        return _rights_page(
            request, who,
            error=_("Права роли не изменены: база не приняла запись."),
            status=403,
        )

    request.session["roles_notice"] = _("Права роли «%(role)s» сохранены.") % {"role": _title(role)}
    return redirect(reverse("roles"))


INVITED = "invited"
GRANTED = "granted"
REVOKED = "revoked"


def _record(who, *, subject, action: str, role, until, reason: str) -> None:
    """Записать событие доступа в историю.

    Пишется в той же транзакции, что и само событие (у проекта включён
    `ATOMIC_REQUESTS`): роль, выданная без записи в истории, — это ровно то
    состояние, из-за которого задача и появилась.

    Автор берётся из контекста, а не из формы. Подставить чужое имя нельзя и
    физически: политика `roles_manager_write` сверяет `actor_user_id` с
    `app_user_id()`, и запись «Х выдал роль» ценна ровно этим.
    """
    AccessLogEntry.objects.create(
        tenant_id=who.tenant_id,
        actor_user_id=who.user_id,
        subject_user_id=subject,
        action=action,
        role_id=role.pk,
        # Снимок названия: роль переименуют, а запись обязана читаться прежней.
        role_title=role.title,
        until=until,
        reason=reason,
    )


def _term(request):
    """Срок из формы: дата или пусто. Прошедшая дата — отказ словами.

    Роль «до вчера» — это доступ, которого не было ни секунды: человек нажал
    «выдать» и не выдал ничего, а экран сказал бы «выдана». Молчание такого
    рода здесь дороже всего: речь о правах.
    """
    raw = (request.POST.get("until") or "").strip()
    if not raw:
        return None, ""
    try:
        until = date.fromisoformat(raw)
    except ValueError:
        return None, _("Срок роли записан не датой.")
    if until < date.today():
        return None, _("Срок роли уже прошёл — такой доступ не начнётся.")
    return until, ""


def _reason(request):
    """Причина из формы. Пустая не принимается — по тому же доводу, что у отката.

    Откат утверждения периода без причины продукт не принимает с T025: история,
    которая не отвечает на «зачем», не отвечает ни на что. Доступ — то же
    самое, и эталон подписывает поле теми же словами: «останется в истории
    рядом с вашим именем».
    """
    return (request.POST.get("reason") or "").strip()


def _refusal_page(request, refusal):
    return render(
        request, "web/roles/denied.html",
        {"message": refusal.message}, status=refusal.http_status,
    )


def _insert_person(person_id, *, full_name: str, email: str) -> None:
    """Завести учётку — запросом, а не через ORM, и это не каприз.

    Django к любой вставке дописывает `returning` ради колонок с `db_default`
    (`created_at`, `is_active`). А `returning` заставляет Postgres применить к
    новой строке ещё и политики **чтения** — и вот их-то свежая учётка пройти не
    может: видимой её делает членство, а членства ещё нет и быть не может, оно
    заводится следующей строкой этой же транзакции.

    Расширить политику чтения под этот случай нельзя, не заплатив дорого: любое
    условие, охватывающее строку без членства, открывает тому, кто ведёт роли,
    учётки людей ЧУЖИХ партнёров — вместе с хэшем пароля, который лежит в той
    же таблице. Поэтому дешевле обойтись без `returning`.

    **Пароля здесь нет намеренно.** Как человек получает первый вход — открытый
    вопрос продукта (пароль против кода на почту), и решать его побочным
    эффектом приглашения нельзя. `make_password(None)` даёт непригодный хэш: с
    ним не сойдётся ни один ввод, то есть учётка заведена, но не открыта.

    Логин — почта: второго имени для входа человеку заводить незачем.
    """
    with connection.cursor() as cur:
        cur.execute(
            """insert into users (id, username, full_name, email, password)
               values (%s, %s, %s, %s, %s)""",
            [str(person_id), email, full_name, email, make_password(None)],
        )


def _mail_refused(request, who):
    """Отказ по почте — один на «уже есть у вас» и «занята у другого партнёра».

    Учётки чужих партнёров политики не показывают, поэтому проверка выше видит
    только своих, а чужая почта всплывает на вставке. Отвечать по-разному значит
    сообщать, что такая почта в системе есть — у кого-то. Поэтому текст и код
    ответа одни, те же 400, что у прочих отказов ввода, и по ним нельзя понять
    ни чья почта, ни есть ли она вовсе (решение блока, 30.09.2026; как D023).
    Своего человека администратор найдёт в списке людей — туда текст и ведёт.
    """
    return _invite_page(
        request, who,
        error=_("Эту почту пригласить нельзя. Если человек уже работает у вас, "
                "он есть в списке людей; если нет — проверьте адрес."),
        status=400,
    )


@login_required
def invite(request):
    """Завести человека партнёра и сразу дать ему роль (T188, issue #178).

    До этого учётки появлялись только сидом или руками в базе — то есть завести
    сотрудника партнёр не мог вовсе.

    **Пароля здесь нет намеренно.** Как человек получает первый вход — открытый
    вопрос Q026 (пароль или одноразовый код на почту), и решать его побочным
    эффектом этой формы нельзя. Учётка заводится непригодным для входа хэшем, а
    экран говорит об этом словами, а не оставляет человека гадать.
    """
    who, refused = _guard(request)
    if refused:
        return refused
    if request.method != "POST":
        return _invite_page(request, who)

    full_name = (request.POST.get("full_name") or "").strip()
    email = (request.POST.get("email") or "").strip().lower()
    reason = _reason(request)
    role_id = request.POST.get("role") or ""
    role = Role.objects.filter(pk=role_id, tenant_id=who.tenant_id).first() if role_id else None

    if not full_name or not email:
        return _invite_page(
            request, who,
            error=_("Имя и почта обязательны: без них человека не отличить от другого."),
            status=400,
        )
    if role is None:
        return _invite_page(request, who, error=_("Такой роли у этого партнёра нет."), status=400)
    if not reason:
        return _invite_page(
            request, who,
            error=_("Причина обязательна: она остаётся в истории рядом с вашим именем."),
            status=400,
        )
    if User.objects.filter(email=email).exists() or User.objects.filter(username=email).exists():
        return _mail_refused(request, who)

    until, refused_term = _term(request)
    if refused_term:
        return _invite_page(request, who, error=refused_term, status=400)

    unit_ids, refused_unit = _membership_units(request, who, role)
    if refused_unit:
        return _invite_page(request, who, error=refused_unit, status=400)

    person_id = uuid4()
    try:
        with transaction.atomic():
            _insert_person(person_id, full_name=full_name, email=email)
    except IntegrityError:
        # Почта занята учёткой, которую политики этому партнёру не показывают,
        # — то есть чужой. Ответ тот же, что на свою (`_mail_refused`).
        return _mail_refused(request, who)
    with transaction.atomic():
        Membership.objects.create(
            tenant_id=who.tenant_id, user_id=person_id, role_id=role.pk, expires_at=until,
            unit_ids=unit_ids,
        )
        _record(who, subject=person_id, action=INVITED, role=role,
                until=until, reason=reason)

    request.session["roles_notice"] = _(
        "%(name)s заведён с ролью «%(role)s». Войти он пока не сможет: "
        "как человек получает первый вход, у продукта ещё не решено."
    ) % {"name": full_name, "role": _title(role)}
    # На страницу людей, а не обратно в пустую форму: человек пришёл завести
    # сотрудника и должен увидеть его в списке — с ролью и ссылкой на него.
    return redirect(reverse("roles-people"))


@login_required
def person_roles(request, user_id):
    """Роли одного человека: посмотреть, выдать, снять — со сроком, причиной и следом.

    Человек ищется среди людей ЭТОГО партнёра до любой записи. Прежде адрес
    принимал любой идентификатор, и роль можно было выдать учётке, у которой
    здесь нет ни одной роли, — то есть привязать к своему пространству человека
    чужого партнёра, о котором экран ничего не знает и показать его не может.
    """
    who, refused = _guard(request)
    if refused:
        return refused

    person = next(iter(_people(who.tenant_id, user_id=user_id)), None)
    if person is None:
        # Теми же словами для «нет вовсе» и «есть, но у другого партнёра»: по
        # ответу нельзя понять, чья это учётка (D023).
        return _people_page(
            request, who, error=_("Такого человека у этого партнёра нет."), status=404,
        )
    if request.method != "POST":
        return _person_page(request, who, person)

    role_id = request.POST.get("role") or ""
    role = Role.objects.filter(pk=role_id, tenant_id=who.tenant_id).first() if role_id else None
    if role is None:
        return _person_page(request, who, person,
                            error=_("Такой роли у этого партнёра нет."), status=400)

    reason = _reason(request)
    if not reason:
        return _person_page(
            request, who, person,
            error=_("Причина обязательна: она остаётся в истории рядом с вашим именем."),
            status=400,
        )

    here = reverse("person-roles", args=[user_id])
    if request.POST.get("action") == "remove":
        held = Membership.objects.filter(tenant_id=who.tenant_id, user_id=user_id)
        if not held.filter(role_id=role.pk).exists():
            # Снимать нечего — и записывать в историю нечего: запись «снял
            # роль», которой не было, врёт о том, что с доступом происходило.
            return _person_page(request, who, person,
                                error=_("Этой роли у человека нет."), status=409)
        if held.count() <= 1:
            # Человек без единой роли перестаёт существовать для продукта: он
            # входит и не видит ничего, включая объяснения почему.
            return _person_page(
                request, who, person,
                error=_("Это единственная роль человека — снимать её некуда."),
                status=409,
            )
        with transaction.atomic():
            held.filter(role_id=role.pk).delete()
            _record(who, subject=user_id, action=REVOKED, role=role,
                    until=None, reason=reason)
        request.session["roles_notice"] = _("Роль «%(role)s» снята.") % {"role": _title(role)}
        return redirect(here)

    until, refused_term = _term(request)
    if refused_term:
        return _person_page(request, who, person, error=refused_term, status=400)

    already = Membership.objects.filter(
        tenant_id=who.tenant_id, user_id=user_id, role_id=role.pk,
    ).exists()
    if already:
        return _person_page(request, who, person,
                            error=_("Эта роль у человека уже есть."), status=409)

    unit_ids, refused_unit = _membership_units(request, who, role)
    if refused_unit:
        return _person_page(request, who, person, error=refused_unit, status=400)

    with transaction.atomic():
        Membership.objects.create(
            tenant_id=who.tenant_id, user_id=user_id, role_id=role.pk, expires_at=until,
            unit_ids=unit_ids,
        )
        _record(who, subject=user_id, action=GRANTED, role=role,
                until=until, reason=reason)
    request.session["roles_notice"] = _("Роль «%(role)s» выдана.") % {"role": _title(role)}
    return redirect(here)
