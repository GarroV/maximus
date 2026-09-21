"""
Паспорт стенда обязан УЗНАВАТЬ стенд, а не помнить его наизусть.

Куплено 21.09.2026. Продукт переименовали из «Dodo P&L» в MAXIMUS, а на
площадке остались прежние имена: каталог `dodo_pnl_service`, compose-проект
`dodo-pnl`, метка демо-базы `dodo-pnl-demo`. `tools/stand.py` держал имя
`maximus` константой — и на работающий стенд отвечал «ни одного контейнера,
стенд не поднят». Скрипт, написанный против утверждений на память, сам стал
утверждением на память; заметили, только когда владелец открыл ссылку и увидел
502.

Поэтому здесь проверяется не вывод, а два свойства: имя стенда в исходник не
зашито, и неопознанный стенд заканчивается отказом, а не догадкой.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import stand  # noqa: E402


def test_the_tool_does_not_remember_the_stand_name_by_heart():
    """Ни имени продукта, ни имени каталога стенда в исполняемом коде.

    Смотрим на код без комментариев и докстрок: в пояснениях имена как раз
    нужны — там объясняется, чем кончилось хранение имени наизусть. А вот
    обычные строковые литералы проверяются обязательно: прежний хардкод жил
    именно в них (`DEFAULT_PREFIX = "maximus"`), и проверка, которая их
    пропускает, зелена на сломанном коде — так и вышло с первой попыткой.
    """
    import ast

    source = (ROOT / "tools" / "stand.py").read_text()
    tree = ast.parse(source)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    docstrings.add(id(first.value))

    literals = [
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]

    for forbidden in ("maximus", "dodo-pnl", "dodo_pnl"):
        hits = [text for text in literals if forbidden in text]
        assert not hits, (
            f"имя {forbidden!r} зашито в tools/stand.py ({hits}) — "
            f"после переименования скрипт снова начнёт врать"
        )


def test_an_unrecognised_stand_is_a_refusal_not_a_guess(monkeypatch):
    """Каталога с нашим репозиторием нет — отказ словами, а не умолчание."""
    monkeypatch.setattr(stand, "local", lambda sh, timeout=40: "abc1234\n")
    monkeypatch.setattr(stand, "remote", lambda ps, timeout=40: "someone-else=deadbeef\n")

    with pytest.raises(stand.StandNotFound) as refused:
        stand.discover(stand.remote, is_remote=True)

    assert "не развёрнут" in str(refused.value)


def test_the_stand_is_found_by_the_repository_passport_whatever_it_is_called(monkeypatch):
    """Каталог и compose-проект зовутся как угодно — находим по корневому коммиту."""
    monkeypatch.setattr(stand, "local", lambda sh, timeout=40: "abc1234\n")
    monkeypatch.setattr(
        stand, "remote",
        lambda ps, timeout=40: (
            '{"name": "old-name"}' if "compose config" in ps
            else "someone-else=deadbeef\nold_folder=abc1234\n"
        ),
    )

    path, prefix = stand.discover(stand.remote, is_remote=True)

    assert path.endswith("old_folder")
    assert prefix == "old-name"


def test_compose_that_names_no_project_is_a_refusal(monkeypatch):
    """Compose промолчал — значит неизвестно, что здесь наше. Не гадаем."""
    with pytest.raises(stand.StandNotFound):
        stand.compose_name(lambda sh, timeout=40: "", "cd x;", "x")


def test_ours_is_told_apart_by_the_ports_our_containers_actually_hold():
    """Свои порты берутся у живых контейнеров, а не из списка в коде."""
    ours = [
        "x-app-1|Up|0.0.0.0:8030->8000/tcp, [::]:8030->8000/tcp",
        "x-db-1|Up|127.0.0.1:5440->5432/tcp",
        "x-worker-1|Up|8000/tcp",
    ]

    assert stand.published_ports(ours) == {"8030", "5440"}
    assert stand.published_ports([]) == set()


@pytest.mark.parametrize(
    "answer, verdict",
    [
        ("12\n", "12"),
        ("Error response from daemon: No such container: x-demo-1", "нет контейнера"),
        ("grep: /app/src/web/static/web/tokens.css: No such file or directory", "нет файла"),
    ],
)
def test_a_missing_container_is_not_the_same_as_a_stale_image(answer, verdict):
    """Три разных ответа не сливаются в один — иначе вывод называет чужую причину."""
    assert stand.design_system(lambda sh, timeout=40: answer, "x", "demo") == verdict
