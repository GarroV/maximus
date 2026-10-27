"""Учётная запись: кто вы, что вам видно, пароль и гайд (T222).

Отдельной страницей, а не пунктами внизу левой панели. Панель обязана
вмещать все разделы роли без прокрутки на экране высотой 900 (сверка с
эталоном по T222): подвал с «Паролем» и «Как это работает» отдельными
пунктами выталкивал за край целую область учёта. Здесь то, что человек
открывает редко, — и одна страница делает одно дело: говорит человеку о
его учётной записи и ведёт туда, где её меняют.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

# Что сказать после привязки или отвязки Google (T241). Ключ — в адресе,
# текст — здесь: текст в адресе подменялся бы ссылкой.
GOOGLE_DONE = {
    "linked": _("Аккаунт Google привязан: теперь можно входить кнопкой «Войти через Google»."),
    "unlinked": _("Аккаунт Google отвязан: вход — логином и паролем."),
}


@login_required
def page(request):
    from .google_login import REFUSALS

    shown = request.GET.get("google", "")
    return render(request, "web/account.html", {
        # Раздел Google — только когда вход через Google настроен (T241, D098).
        "google_enabled": getattr(settings, "GOOGLE_AUTH", None) is not None,
        "google_linked": bool(getattr(request.user, "google_sub", None)),
        "google_done": GOOGLE_DONE.get(shown, ""),
        "google_refused": REFUSALS.get(shown, "") if shown not in GOOGLE_DONE else "",
    })
