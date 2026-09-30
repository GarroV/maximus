#!/usr/bin/env python3
"""
Паспорт стенда: что из работающего — НАШЕ, какой оно версии и что видно снаружи.

Зачем этот скрипт существует. Площадка MUSPELHEIM общая: на ней рядом живут
чужие проекты, и один из них занимает корень публичного адреса. 22.08.2026 из-за
этого случились две ошибки подряд, обе — утверждения без проверки:

  1. «Демо уже выглядит как продукт» — на стенде стоял образ пятидневной
     давности, куда дизайн-система не доехала вовсе;
  2. «Публичный адрес уже включён» — включён он был для ЧУЖОГО проекта, и
     владелец, открыв ссылку, увидел не наш продукт.

Оба факта проверяются одной командой за десять секунд. Поэтому: прежде чем
сказать что-либо о стенде — прогнать этот скрипт и говорить по его выводу.

    python tools/stand.py                # площадка MUSPELHEIM
    python tools/stand.py --host local   # своя машина

Скрипт только читает: ни одного docker-действия, ни одной записи.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Где на площадке живут проекты — и наш, и чужие. Единственное, что скрипт
# знает про площадку заранее: как называется наш каталог и как называется наш
# compose-проект, он выясняет на месте (см. `discover`).
PROJECTS_DIR = r"C:\projects"

OK, WARN, BAD, DIM = "\033[32m", "\033[33m", "\033[31m", "\033[90m"
BOLD, OFF = "\033[1m", "\033[0m"


def run(cmd: list[str], timeout: int = 40) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or "") + (out.stderr or "")
    except subprocess.TimeoutExpired:
        return "__TIMEOUT__"


def remote(ps: str, timeout: int = 40) -> str:
    """Выполнить PowerShell на площадке. Дефолтный шелл там cmd — отсюда обёртка."""
    return run(["ssh", "-o", "ConnectTimeout=10", "muspelheim",
                f'powershell -NoProfile -Command "{ps}"'], timeout)


def local(sh: str, timeout: int = 40) -> str:
    return run(["bash", "-lc", sh], timeout)


def head(text: str) -> None:
    print(f"\n{BOLD}{text}{OFF}")


class StandNotFound(RuntimeError):
    """Стенд не опознан. Отказ словами — вместо догадки о том, что наше."""


def root_commits(call, cd: str) -> set[str]:
    """Корневые коммиты репозитория — его паспорт.

    Именно они, а не имя: продукт переименовали из «Dodo P&L» в MAXIMUS, и
    каталог, compose-проект и метка демо-базы на площадке остались прежними.
    Скрипт, помнивший имя наизусть, после этого говорил «стенд не поднят» про
    работающий стенд — то есть ровно то враньё, ради отлова которого написан.
    Корневой коммит переименование не меняет.
    """
    out = call(f"{cd} git rev-list --max-parents=0 HEAD")
    lines = (line.strip() for line in out.splitlines())
    return {line for line in lines if re.fullmatch(r"[0-9a-f]{7,40}", line)}


def discover(call, is_remote: bool) -> tuple[str, str]:
    """Найти НАШ каталог и имя compose-проекта на площадке: (путь, префикс).

    Ищем по паспорту репозитория, перебирая каталоги площадки. Чужие каталоги
    при этом только читаются — ни одной записи, ни одного docker-действия.
    """
    if not is_remote:
        prefix = compose_name(local, f"cd {ROOT} &&", str(ROOT))
        return str(ROOT), prefix

    mine = root_commits(local, f"cd {ROOT} &&")
    if not mine:
        raise StandNotFound("не удалось прочитать корневой коммит своего репозитория")

    listing = remote(
        f"Get-ChildItem {PROJECTS_DIR} -Directory | ForEach-Object {{ "
        f"$p = $_.FullName; if (Test-Path \\\"$p\\.git\\\") {{ "
        f"$r = (git -C $p rev-list --max-parents=0 HEAD 2>$null) -join ','; "
        f"if ($r) {{ Write-Output \\\"$($_.Name)=$r\\\" }} }} }}",
        timeout=90,
    )
    for line in listing.splitlines():
        name, _, commits = line.strip().partition("=")
        if not commits:
            continue
        if mine & {c.strip() for c in commits.split(",")}:
            path = f"{PROJECTS_DIR}\\{name}"
            return path, compose_name(remote, f"cd {path};", path)

    raise StandNotFound(
        f"в {PROJECTS_DIR} нет каталога с этим репозиторием — "
        f"стенд не развёрнут либо развёрнут не там"
    )


def compose_name(call, cd: str, path: str) -> str:
    """Имя compose-проекта = префикс имён наших контейнеров.

    Спрашиваем у самого compose, а не у `.env`: имя может прийти и оттуда, и из
    имени каталога, и правило нормализации (нижний регистр, точки долой) — его,
    а не наше. Всё, что не начинается с этого префикса, принадлежит чужому
    проекту и не наше дело — ни смотреть, ни трогать.
    """
    out = call(f"{cd} docker compose config --no-normalize --format json", timeout=90)
    m = re.search(r'"name"\s*:\s*"([^"]+)"', out)
    if m:
        return m.group(1)
    raise StandNotFound(f"compose в {path} не назвал имя проекта — не знаю, что здесь наше")


def containers(call, prefix: str) -> tuple[list[str], int]:
    """Наши контейнеры отдельно, чужие — только числом."""
    raw = call("docker ps --format '{{.Names}}|{{.Status}}|{{.Ports}}'")
    ours, alien = [], 0
    for line in raw.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        name = line.split("|", 1)[0]
        (ours.append(line) if name.startswith(prefix) else None)
        alien += 0 if name.startswith(prefix) else 1
    return ours, alien


def published_ports(ours: list[str]) -> set[str]:
    """Порты хоста, которые заняли НАШИ контейнеры: ими и опознаётся своё."""
    ports: set[str] = set()
    for line in ours:
        parts = line.split("|")
        if len(parts) > 2:
            ports.update(re.findall(r":(\d+)->", parts[2]))
    return ports


def version(call, cd: str) -> dict:
    """Версия кода на стенде против origin/master."""
    here = call(f"{cd} git log -1 --format='%h|%ad|%s' --date=short").strip().splitlines()
    counting = f"{cd} git fetch origin -q; {cd} git rev-list --count HEAD..origin/master"
    behind = call(counting).strip().splitlines()
    commit = next((row for row in here if "|" in row), "?|?|?")
    count = next((row for row in reversed(behind) if row.strip().isdigit()), "?")
    return {"commit": commit, "behind": count}


def design_system(call, prefix: str, service: str = "app") -> str:
    """Доехала ли дизайн-система в работающий образ — считаем токены внутри.

    Три разных ответа, и они не сливаются в один. «Контейнера нет» — это не
    «образ старее дизайн-системы»: первое означает, что стенд не поднят, второе
    — что поднят не тот образ. Раньше оба показывались как второе, и вывод
    уверенно называл причину, которой не было.
    """
    # Через grep, а не python -c: вложенные кавычки не переживают путь
    # bash → ssh → PowerShell → docker exec и молча ломаются (проверено).
    out = call(f"docker exec {prefix}-{service}-1 grep -c -E '^[[:space:]]*--' "
               f"/app/src/web/static/web/tokens.css")
    m = re.search(r"^\s*(\d+)\s*$", out, re.M)
    if m:
        return m.group(1)
    if re.search(r"No such container|is not running", out, re.I):
        return "нет контейнера"
    return "нет файла"


def funnel(prefix: str) -> list[tuple[str, str]]:
    """Что опубликовано наружу и чьё это. Только для площадки."""
    raw = remote("tailscale funnel status")
    rows: list[tuple[str, str]] = []
    addr = ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("https://"):
            addr = line.split()[0]
        m = re.search(r"proxy\s+(http://\S+)", line)
        if m:
            rows.append((addr or "?", m.group(1)))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Паспорт стенда: наше, версия, публичность")
    ap.add_argument("--host", choices=["muspelheim", "local"], default="muspelheim")
    ap.add_argument("--prefix", help="имя compose-проекта, если автопоиск ошибся")
    ap.add_argument("--path", help="каталог стенда, если автопоиск ошибся")
    args = ap.parse_args()

    is_remote = args.host == "muspelheim"
    call = remote if is_remote else local

    probe = call("docker ps --format '{{.Names}}'")
    if probe == "__TIMEOUT__" or "docker" in probe and "not recognized" in probe:
        print(f"{BAD}Площадка недоступна или docker не отвечает.{OFF}")
        return 2

    try:
        found_path, found_prefix = discover(call, is_remote)
    except StandNotFound as refusal:
        if not (args.prefix and args.path):
            # Молчать нельзя: скрипт для того и есть, чтобы про стенд не
            # приходилось догадываться. Не опознали — отказ, а не умолчание.
            print(f"{BAD}Стенд не опознан: {refusal}{OFF}")
            print(f"  {DIM}знаю точно — передайте --path и --prefix{OFF}")
            return 2
        found_path, found_prefix = args.path, args.prefix
    path = args.path or found_path
    prefix = args.prefix or found_prefix

    print(f"{BOLD}Стенд {args.host}{OFF} · наше = контейнеры с префиксом {prefix!r}")
    print(f"{DIM}каталог стенда: {path}{OFF}")

    head("Наши контейнеры")
    ours, alien = containers(call, prefix)
    if not ours:
        print(f"  {BAD}ни одного — стенд не поднят{OFF}")
    for line in ours:
        name, status, ports = (line.split("|") + ["", ""])[:3]
        mark = OK if "Up" in status else BAD
        print(f"  {mark}●{OFF} {name:26} {status:22} {DIM}{ports}{OFF}")
    print(f"  {DIM}рядом чужих контейнеров: {alien} — не наши, не трогать{OFF}")

    head("Версия кода на стенде")
    v = version(call, f"cd {path};" if is_remote else f"cd {path} &&")
    c, d, subj = (v["commit"].split("|") + ["", ""])[:3]
    behind = v["behind"]
    fresh = behind == "0"
    print(f"  {c} от {d} — {subj[:70]}")
    verdict = (OK + "совпадает с origin/master" if fresh
               else WARN + f"ОТСТАЁТ от origin/master на {behind} коммитов")
    print(f"  {verdict}{OFF}")

    head("Дизайн-система в работающих образах")
    ref = sum(1 for line in (ROOT / "src/web/static/web/tokens.css").read_text().splitlines()
              if line.strip().startswith("--"))
    # Демо живёт под отдельным профилем compose и на обычном `up -d` остаётся
    # на прежнем образе — молча. Именно так стенд и разъехался 22.08.
    for service, title in (("app", "приложение"), ("demo", "демо")):
        tokens = design_system(call, prefix, service)
        if tokens.isdigit():
            same = int(tokens) == ref
            print(f"  {title:12} токенов {tokens} против {ref} в репозитории "
                  f"{OK + '— совпадает' if same else WARN + '— РАСХОДЯТСЯ'}{OFF}")
        elif tokens == "нет контейнера":
            print(f"  {title:12} {BAD}контейнера нет — стенд не поднят (профиль demo?){OFF}")
        else:
            print(f"  {title:12} {BAD}tokens.css нет — образ старее дизайн-системы{OFF}")

    if is_remote:
        head("Что видно из интернета")
        rows = funnel(prefix)
        if not rows:
            print(f"  {DIM}Funnel ничего не публикует{OFF}")
        # Чьё это, решают порты НАШИХ контейнеров, а не список в коде: список
        # устаревает молча, и тогда своё показывается чужим или наоборот.
        # Контейнеры стоят — портов нет, и про чужое мы честно не знаем.
        mine_ports = published_ports(ours)
        for addr, target in rows:
            port = re.search(r":(\d+)", target)
            p = port.group(1) if port else ""
            if not mine_ports:
                tag = f"{DIM}стенд не поднят — чьё это, сказать нечем{OFF}"
            else:
                tag = f"{OK}НАШЕ{OFF}" if p in mine_ports else f"{BAD}ЧУЖОЙ ПРОЕКТ{OFF}"
            print(f"  {addr}  →  {target}   {tag}")
        published = {re.search(r":(\d+)", t).group(1) for _, t in rows if re.search(r":(\d+)", t)}
        if mine_ports and not (mine_ports & published):
            print(f"  {WARN}наш продукт наружу НЕ опубликован — "
                  f"по публичному адресу отвечает не он{OFF}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
