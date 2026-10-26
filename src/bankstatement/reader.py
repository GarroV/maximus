"""Файл банка → `Statement`. Формат угадывается по содержимому, а не по расширению.

Расширение врёт часто: «izvod.xml» из одного банка и из другого — разные
форматы, а xlsx бывает выгружен с расширением .xls. Поэтому XML узнаётся по
корню, таблица — по заголовкам, а книга Excel — по подписи zip-архива.

**Отказ — словами и сразу.** Файл, из которого не прочиталось ни одной
строки, не загружается «пустой выпиской»: пустая выписка выглядит как месяц без
движения денег, а это ложь, которую никто не заметит.
"""
from __future__ import annotations

import csv
import io
import unicodedata
from functools import cache
from pathlib import Path
from xml.etree import ElementTree

import yaml

from . import camt
from .model import IN, OUT, Line, Statement
from .values import amount, day

FORMATS = Path(__file__).with_name("formats.yaml")
# Выписка за месяц у самого оборотистого партнёра — сотни килобайт. Пять
# мегабайт — запас в десять раз, а не приглашение грузить архив за год.
MAX_BYTES = 5 * 1024 * 1024
XLSX_SIGNATURE = b"PK\x03\x04"


class Unreadable(Exception):
    """Файл не разобран. Текст показывается человеку как есть."""


@cache
def formats() -> dict:
    return yaml.safe_load(FORMATS.read_text(encoding="utf-8"))


def read(content: bytes, file_name: str = "") -> Statement:
    if not content:
        raise Unreadable("Файл пустой.")
    if len(content) > MAX_BYTES:
        raise Unreadable("Файл больше 5 МБ — это не выписка за месяц.")
    if content.startswith(XLSX_SIGNATURE):
        statement = _table(_xlsx_rows(content))
    elif content.lstrip()[:1] == b"<":
        statement = _xml(content)
    else:
        statement = _table(_csv_rows(content))
    if not statement.lines:
        raise Unreadable("В файле нет ни одной операции.")
    return statement


# --- XML ------------------------------------------------------------------------


def _xml(content: bytes) -> Statement:
    # Объявлений сущностей в выписке не бывает, а в атаке на разборщик XML
    # («миллиард смехов», внешние сущности) — бывают всегда. Отказываем по
    # самому объявлению, не полагаясь на то, что разборщик его переживёт.
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise Unreadable("XML с объявлением сущностей не принимается.")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise Unreadable(f"XML не читается: {error}.") from error
    if _local(root.tag) == "Document" and "camt.053" in root.tag:
        return camt.read(root)
    for profile in formats()["xml"]:
        found = root if _local(root.tag) == profile["root"] else _first(root, profile["root"])
        if found is not None:
            return _profiled(found, profile)
    raise Unreadable(
        "XML этого банка пока не знаком: знаем camt.053 и izvod iBank и Raiffeisen. "
        "Пришлите образец — формат заводится профилем."
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(node, name: str):
    for child in node.iter():
        if _local(child.tag) == name:
            return child
    return None


def _path(node, path: str) -> str:
    """Текст по пути «a/b» или атрибут «a@b» — без пространств имён."""
    element_path, _, attribute = path.partition("@")
    current = node
    for step in [s for s in element_path.split("/") if s]:
        current = next((c for c in current if _local(c.tag) == step), None)
        if current is None:
            return ""
    if attribute:
        return (current.get(attribute) or "").strip()
    return (current.text or "").strip()


def _profiled(root, profile: dict) -> Statement:
    fields = profile["fields"]
    by_attribute = profile.get("attributes", False)

    def value(record, name: str) -> str:
        if by_attribute:
            return (record.get(name) or "").strip()
        return _path(record, name)

    def first_of(record, names) -> str:
        for name in names:
            found = value(record, name)
            if found:
                return found
        return ""

    lines = []
    for number, record in enumerate(
        (r for r in root.iter() if _local(r.tag) == profile["record"]), start=1
    ):
        try:
            on = day(first_of(record, fields["date"]))
            if "amount" in fields:
                sum_ = amount(value(record, fields["amount"]))
                direction = _direction(value(record, fields["direction"]), profile)
            else:
                sum_, direction = _two_columns(
                    amount(value(record, fields["debit"])),
                    amount(value(record, fields["credit"])),
                )
        except ValueError as error:
            raise Unreadable(f"Операция {number}: {error}.") from error
        if on is None or sum_ is None or direction is None:
            raise Unreadable(f"Операция {number}: нет даты, суммы или направления.")
        def optional(name: str, record=record) -> str:
            return value(record, fields[name]) if fields.get(name) else ""

        lines.append(Line(
            op_date=on, direction=direction, amount=abs(sum_),
            party_name=optional("party_name"), party_account=optional("party_account"),
            purpose=optional("purpose"), bank_ref=optional("bank_ref"),
        ))
    account = _profile_account(root, profile.get("account", ""))
    currency = _profile_account(root, profile.get("currency", ""))
    return Statement(format=profile["code"], lines=lines, account=account, currency=currency,
                     period_from=min((x.op_date for x in lines), default=None),
                     period_to=max((x.op_date for x in lines), default=None))


def _profile_account(root, spec: str) -> str:
    """Значение шапки по профилю: «тег» или «тег@атрибут» — счёт, валюта."""
    if not spec:
        return ""
    tag, _, attribute = spec.partition("@")
    found = root if _local(root.tag) == tag else _first(root, tag)
    if found is None:
        return ""
    return (found.get(attribute) if attribute else found.text or "").strip()


def _direction(raw: str, profile: dict) -> str | None:
    values = profile.get("direction_values", {})
    text = raw.strip().casefold()
    if text in values.get("out", []):
        return OUT
    if text in values.get("in", []):
        return IN
    return None


def _two_columns(debit, credit):
    """«На терет» и «в корист» — две колонки; заполнена ровно одна."""
    debit = debit or 0
    credit = credit or 0
    if debit and not credit:
        return debit, OUT
    if credit and not debit:
        return credit, IN
    if debit and credit:
        raise ValueError("заполнены обе колонки — и расход, и приход")
    return None, None


# --- таблица --------------------------------------------------------------------


def _csv_rows(content: bytes) -> list[list]:
    for encoding in ("utf-8-sig", "cp1250", "cp1251"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise Unreadable("Кодировка файла не распознана: ждём UTF-8 или Windows-1250/1251.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = _Semicolon if sample.count(";") >= sample.count(",") else csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


class _Semicolon(csv.excel):
    delimiter = ";"


def _xlsx_rows(content: bytes) -> list[list]:
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as error:  # openpyxl бросает что угодно на битом архиве
        raise Unreadable("Книга Excel не открывается.") from error
    sheet = book.worksheets[0]
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def _header_key(text) -> str:
    plain = unicodedata.normalize("NFKD", str(text or "")).casefold()
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return " ".join("".join(ch if ch.isalnum() else " " for ch in plain).split())


@cache
def _synonyms() -> dict[str, str]:
    known = {}
    for field, names in formats()["table"].items():
        for name in names:
            known[_header_key(name)] = field
    return known


def _find_header(rows: list[list]) -> tuple[int, dict[str, int], list[tuple[str, str]]]:
    """Строка заголовков: первая, где узнаны дата и хотя бы одна сумма.

    Ищем, а не берём первую: банки ставят над таблицей шапку — номер выписки,
    счёт, период, — и её строки заголовками не являются.
    """
    synonyms = _synonyms()
    for index, row in enumerate(rows[:30]):
        columns: dict[str, int] = {}
        mapping = []
        for position, cell in enumerate(row):
            field = synonyms.get(_header_key(cell))
            if field and field not in columns:
                columns[field] = position
                mapping.append((str(cell).strip(), field))
        has_sum = "amount" in columns or "debit" in columns or "credit" in columns
        if "date" in columns and has_sum:
            return index, columns, mapping
    raise Unreadable(
        "Не нашли колонки даты и суммы. Узнаём заголовки вроде «Datum valute», "
        "«Iznos na teret», «Iznos u korist» — проверьте, что в файле есть строка заголовков."
    )


def _table(rows: list[list]) -> Statement:
    header, columns, mapping = _find_header(rows)

    def cell(row, field):
        position = columns.get(field)
        if position is None or position >= len(row):
            return None
        return row[position]

    lines = []
    for number, row in enumerate(rows[header + 1:], start=header + 2):
        if not any(str(c).strip() for c in row if c is not None):
            continue
        try:
            on = day(cell(row, "date"))
            if "amount" in columns:
                signed = amount(cell(row, "amount"))
                sum_, direction = (None, None) if signed is None else (
                    abs(signed), OUT if signed < 0 else IN)
            else:
                sum_, direction = _two_columns(amount(cell(row, "debit")),
                                               amount(cell(row, "credit")))
        except ValueError as error:
            raise Unreadable(f"Строка {number}: {error}.") from error
        if on is None and sum_ is None:
            continue  # строка итога или подписи под таблицей
        if on is None or sum_ is None:
            raise Unreadable(f"Строка {number}: нет даты или суммы.")

        def text(field, row=row):
            raw = cell(row, field)
            return "" if raw is None else str(raw).strip()

        lines.append(Line(
            op_date=on, direction=direction, amount=sum_,
            party_name=text("party_name"), party_account=text("party_account"),
            purpose=text("purpose"), bank_ref=text("bank_ref"),
        ))
    return Statement(format="table", lines=lines, mapping=mapping,
                     period_from=min((x.op_date for x in lines), default=None),
                     period_to=max((x.op_date for x in lines), default=None))
