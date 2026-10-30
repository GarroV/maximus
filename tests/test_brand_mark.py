"""Знак продукта во вкладке (#247): у каждой страницы со своим `<head>` и без пропусков.

Проверка по шаблонам, а не по одной отрисованной странице: страниц с
собственной шапкой несколько (продукт, печать, вход в демо, демо на
пересборке), и знак, забытый в одной из них, в прогоне одной страницы не виден.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/web/static/web"
INCLUDE = '{% include "web/components/head_icons.html" %}'


HEAD = re.compile(r"^\s*<head>", re.M)


def head_templates() -> list[Path]:
    return [
        p for p in sorted((ROOT / "src").rglob("templates/**/*.html"))
        if HEAD.search(p.read_text())
    ]


def test_every_page_with_its_own_head_carries_the_mark():
    templates = head_templates()
    assert len(templates) >= 3, f"нашлось меньше страниц с <head>, чем есть в продукте: {templates}"
    missing = [str(p.relative_to(ROOT)) for p in templates if INCLUDE not in p.read_text()]
    assert not missing, f"страницы без знака во вкладке: {missing}"


def test_every_file_the_mark_points_to_is_on_disk():
    include = (ROOT / "src/web/templates/web/components/head_icons.html").read_text()
    named = re.findall(r"static 'web/([^']+)'", include)
    manifest = json.loads((STATIC / "site.webmanifest").read_text())
    named += [icon["src"] for icon in manifest["icons"]]
    absent = [name for name in named if not (STATIC / name).is_file()]
    assert len(named) >= 7 and not absent, f"знак ссылается на файлы, которых нет: {absent}"
