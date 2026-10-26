"""Разбор файла выписки: какой бы формат ни прислал банк, на выходе одни и те же строки.

Форматы взяты из открытых спецификаций (журнал блока facts): ISO 20022
camt.053, XML «izvod» платформы iBank (Banca Intesa) и XML Raiffeisen, плюс
таблица CSV/xlsx с колонками по заголовкам. Все файлы здесь синтетические —
настоящих выписок в публичном репозитории быть не может.

Ключ строки проверяется здесь же: от него зависит, удвоит ли повторная загрузка
деньги, а это ядро.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pytest

from bankstatement.keys import line_keys
from bankstatement.reader import Unreadable, read

CAMT = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
 <BkToCstmrStmt><Stmt>
  <Acct><Id><Othr><Id>160-0000000001-11</Id></Othr></Id><Ccy>RSD</Ccy>
   <Svcr><FinInstnId><Nm>Test Bank</Nm></FinInstnId></Svcr></Acct>
  <FrToDt><FrDtTm>2026-06-01T00:00:00</FrDtTm><ToDtTm>2026-06-30T23:59:59</ToDtTm></FrToDt>
  <Ntry>
   <Amt Ccy="RSD">24000.00</Amt><CdtDbtInd>DBIT</CdtDbtInd>
   <BookgDt><Dt>2026-06-12</Dt></BookgDt><ValDt><Dt>2026-06-12</Dt></ValDt>
   <AcctSvcrRef>REF-0001</AcctSvcrRef>
   <NtryDtls><TxDtls>
    <RltdPties><Cdtr><Nm>EPS Elektro</Nm></Cdtr>
     <CdtrAcct><Id><Othr><Id>840-0000000002-22</Id></Othr></Id></CdtrAcct></RltdPties>
    <RmtInf><Ustrd>struja 05/2026</Ustrd></RmtInf>
   </TxDtls></NtryDtls>
  </Ntry>
  <Ntry>
   <Amt Ccy="RSD">1500.50</Amt><CdtDbtInd>CRDT</CdtDbtInd>
   <BookgDt><Dt>2026-06-13</Dt></BookgDt>
   <NtryDtls><TxDtls>
    <RltdPties><Dbtr><Nm>Kupac doo</Nm></Dbtr></RltdPties>
    <RmtInf><Ustrd>povracaj</Ustrd></RmtInf>
   </TxDtls></NtryDtls>
  </Ntry>
 </Stmt></BkToCstmrStmt>
</Document>
"""

IBANK = """<?xml version="1.0" encoding="UTF-8"?>
<pmtnotification><stmtrs>
 <acctid>160-0000000001-11</acctid><curdef>RSD</curdef>
 <trnlist>
  <stmttrn>
   <fitid>9000123</fitid><benefit>debit</benefit><trnamt>74200.00</trnamt>
   <dtposted>2026-06-03T00:00:00</dtposted><dtavail>2026-06-03T00:00:00</dtavail>
   <payeeinfo><name>ELEKTRODISTRIBUCIJA</name></payeeinfo>
   <payeeaccountinfo><acctid>840-0000000003-33</acctid></payeeaccountinfo>
   <purpose>struja 05/2026</purpose>
  </stmttrn>
 </trnlist>
</stmtrs></pmtnotification>
"""

RAIFFEISEN = """<?xml version="1.0" encoding="UTF-8"?>
<TransakcioniRacunPrivredaIzvod>
 <Zaglavlje Partija="265-0000000004-44" DatumIzvoda="05.06.2026"/>
 <Stavke NalogKorisnik="KOMERC PROMET" BrojRacunaPrimaocaPosiljaoca="265-0000000005-55"
   Opis="faktura 2026-1245" Duguje="94.800,00" Potrazuje="0,00"
   BrojZaReklamaciju="RZB-77" DatumValute="05.06.2026"/>
</TransakcioniRacunPrivredaIzvod>
"""

# Колонки нарочно не в «правильном» порядке: банк меняет порядок, а разбор
# обязан узнавать их по заголовку (эталон, модуль 7).
CSV = (
    "Svrha plaćanja;Iznos na teret;Iznos u korist;Datum valute;Naziv primaoca\n"
    "zakup 06/2026;240.000,00;;15.06.2026;Milan Test\n"
    "uplata;;12.000,00;17.06.2026;Kupac doo\n"
)


def test_camt053_gives_direction_payee_and_bank_reference():
    statement = read(CAMT.encode(), "izvod.xml")
    assert statement.format == "camt053"
    assert statement.account == "160-0000000001-11"
    assert statement.currency == "RSD"
    assert (statement.period_from, statement.period_to) == (date(2026, 6, 1), date(2026, 6, 30))
    out, incoming = statement.lines
    assert (out.direction, out.amount) == ("out", Decimal("24000.00"))
    assert out.op_date == date(2026, 6, 12)
    assert (out.party_name, out.party_account) == ("EPS Elektro", "840-0000000002-22")
    assert out.bank_ref == "REF-0001"
    assert out.purpose == "struja 05/2026"
    # Для прихода контрагент — плательщик, а не получатель.
    assert (incoming.direction, incoming.party_name) == ("in", "Kupac doo")
    assert incoming.bank_ref == ""


def test_the_ibank_izvod_is_read_by_its_profile():
    statement = read(IBANK.encode(), "izvod-2026-06.xml")
    assert statement.format == "ibank"
    assert statement.account == "160-0000000001-11"
    (line,) = statement.lines
    assert (line.direction, line.amount, line.bank_ref) == ("out", Decimal("74200.00"), "9000123")
    assert line.op_date == date(2026, 6, 3)
    assert line.party_name == "ELEKTRODISTRIBUCIJA"


def test_the_raiffeisen_izvod_reads_attributes_and_two_amount_columns():
    statement = read(RAIFFEISEN.encode(), "izvod.xml")
    assert statement.format == "raiffeisen_rs"
    (line,) = statement.lines
    assert (line.direction, line.amount) == ("out", Decimal("94800.00"))
    assert line.op_date == date(2026, 6, 5)
    assert line.bank_ref == "RZB-77"
    assert statement.account == "265-0000000004-44"


def test_a_csv_is_read_by_headers_in_any_order():
    statement = read(CSV.encode("utf-8"), "izvod.csv")
    assert statement.format == "table"
    rent, refund = statement.lines
    assert (rent.direction, rent.amount) == ("out", Decimal("240000.00"))
    assert rent.op_date == date(2026, 6, 15)
    assert rent.purpose == "zakup 06/2026"
    assert (refund.direction, refund.amount) == ("in", Decimal("12000.00"))
    # Как разобраны колонки — показывается человеку (эталон «Как разобраны колонки»).
    assert ("Datum valute", "date") in statement.mapping


def test_an_xlsx_is_read_the_same_way_as_a_csv():
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["Izvod br. 12"])                     # шапка банка над таблицей
    sheet.append(["Datum", "Naziv", "Svrha", "Duguje", "Potražuje"])
    sheet.append([date(2026, 6, 8), "Telekom Test", "internet", 5900, None])
    buffer = io.BytesIO()
    book.save(buffer)
    statement = read(buffer.getvalue(), "izvod.xlsx")
    (line,) = statement.lines
    assert (line.direction, line.amount) == ("out", Decimal("5900.00"))
    assert line.op_date == date(2026, 6, 8)


def test_a_file_without_known_columns_is_refused_by_name():
    with pytest.raises(Unreadable) as refused:
        read(b"a;b;c\n1;2;3\n", "x.csv")
    assert "дат" in str(refused.value).lower()


def test_an_xml_with_an_entity_declaration_is_refused():
    """Объявления сущностей в выписке не бывает; бывает в атаке на разборщик XML."""
    hostile = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    with pytest.raises(Unreadable):
        read(hostile.encode(), "izvod.xml")


def test_an_unknown_xml_is_refused_not_read_as_empty():
    with pytest.raises(Unreadable):
        read(b"<?xml version='1.0'?><other/>", "izvod.xml")


def test_a_statement_without_lines_is_refused():
    with pytest.raises(Unreadable):
        read(b"Datum valute;Iznos na teret;Naziv primaoca\n", "x.csv")


# --- ключ строки ---------------------------------------------------------------


def test_the_bank_reference_is_the_key_when_the_bank_gives_one():
    statement = read(CAMT.encode(), "izvod.xml")
    first, second = line_keys(statement.account, statement.lines)
    again = line_keys(statement.account, read(CAMT.encode(), "copy.xml").lines)
    assert [first, second] == again
    assert first != second


def test_two_identical_payments_in_one_file_are_two_lines():
    """Две одинаковые оплаты в один день — два события, а не дубль."""
    doubled = CSV + "zakup 06/2026;240.000,00;;15.06.2026;Milan Test\n"
    statement = read(doubled.encode(), "x.csv")
    keys = line_keys(statement.account, statement.lines)
    assert len(set(keys)) == 3


def test_the_same_line_in_another_file_has_the_same_key():
    """Выписка за неделю и выписка за месяц пересекаются: общая строка — одна."""
    week = read(CSV.encode(), "week.csv")
    month = read((CSV + "naknada;1.840,00;;24.06.2026;Bank\n").encode(), "month.csv")
    assert set(line_keys("", week.lines)) <= set(line_keys("", month.lines))


def test_the_key_depends_on_the_account():
    statement = read(CSV.encode(), "x.csv")
    assert set(line_keys("acc-1", statement.lines)).isdisjoint(line_keys("acc-2", statement.lines))


def test_the_bank_reference_is_unique_only_within_the_account():
    """Идентификатор операции уникален внутри банка, а не во всём мире."""
    lines = read(CAMT.encode(), "izvod.xml").lines
    assert set(line_keys("acc-1", lines)).isdisjoint(line_keys("acc-2", lines))


# --- файл, который пытается навредить разборщику -------------------------------


def test_a_declaration_is_refused_in_any_encoding():
    """Объявление ловит разборщик, а не поиск по байтам: кодировка его не прячет."""
    hostile = CAMT.replace(
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE Document [<!ENTITY a "x">]>',
    )
    with pytest.raises(Unreadable) as refused:
        read(hostile.encode("utf-16"), "izvod.xml")
    assert "объявлением" in str(refused.value)


def test_a_utf16_statement_without_declarations_is_read():
    plain = CAMT.replace('encoding="UTF-8"', 'encoding="UTF-16"')
    assert read(plain.encode("utf-16"), "izvod.xml").format == "camt053"


def test_an_archive_that_unpacks_into_too_much_is_refused_before_unpacking():
    import zipfile

    from bankstatement.reader import MAX_COMPRESSION_RATIO

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * (MAX_COMPRESSION_RATIO * 20_000))
    with pytest.raises(Unreadable) as refused:
        read(buffer.getvalue(), "izvod.xlsx")
    assert "разворачивается" in str(refused.value)


def test_a_table_longer_than_the_ceiling_is_refused():
    from bankstatement.reader import MAX_ROWS

    row = "naknada;1,00;;24.06.2026;Bank\n"
    huge = "Svrha;Iznos na teret;Iznos u korist;Datum valute;Naziv primaoca\n" + row * MAX_ROWS
    with pytest.raises(Unreadable) as refused:
        read(huge.encode(), "x.csv")
    assert str(MAX_ROWS) in str(refused.value)


def test_an_xlsx_longer_than_the_ceiling_is_refused(monkeypatch):
    from openpyxl import Workbook

    from bankstatement import reader

    monkeypatch.setattr(reader, "MAX_ROWS", 3)
    book = Workbook()
    sheet = book.active
    sheet.append(["Datum", "Duguje"])
    for _ in range(5):
        sheet.append([date(2026, 6, 8), 1])
    buffer = io.BytesIO()
    book.save(buffer)
    with pytest.raises(Unreadable):
        read(buffer.getvalue(), "izvod.xlsx")


def test_a_sheet_of_absurd_width_is_read_only_to_the_column_ceiling():
    """Ячейка в колонке XFD не разворачивает лист в шестнадцать тысяч колонок."""
    from openpyxl import Workbook

    from bankstatement import reader

    book = Workbook()
    sheet = book.active
    sheet.append(["Datum", "Naziv", "Duguje"])
    sheet.append([date(2026, 6, 8), "Telekom Test", 5900])
    sheet.cell(row=2, column=16384, value="x")
    buffer = io.BytesIO()
    book.save(buffer)
    rows = reader._xlsx_rows(buffer.getvalue())
    assert max(len(row) for row in rows) <= reader.MAX_COLS
    (line,) = read(buffer.getvalue(), "izvod.xlsx").lines
    assert line.amount == Decimal("5900.00")


def test_a_long_tail_of_empty_cells_is_not_kept():
    from bankstatement.reader import _capped

    assert _capped([["a", "b"] + [None] * 10_000]) == [["a", "b"]]


def test_the_cell_budget_is_refused_by_name(monkeypatch):
    from bankstatement import reader

    monkeypatch.setattr(reader, "MAX_CELLS", 5)
    with pytest.raises(Unreadable) as refused:
        reader._capped([["1", "2", "3"], ["4", "5", "6"]])
    assert "ячеек" in str(refused.value)


def test_a_slashed_date_is_not_guessed():
    """`01/06/2026` — и 1 июня, и 6 января: дата не угадывается, а называется.

    То же правило, что у загрузки справочника статей (T155): угаданная не туда
    дата выглядит прочитанной из файла, и платёж молча уезжает в другой месяц.
    """
    from bankstatement.values import day

    with pytest.raises(ValueError, match="01/06/2026"):
        day("01/06/2026")


@pytest.mark.parametrize("raw", ["1.840,005", "12.5051", "0.001"])
def test_a_sum_with_more_than_two_decimals_is_refused(raw):
    """Сумма не округляется молча: третий знак после запятой — не копейки,
    а ошибка разбора, и её надо назвать, а не спрятать в округлении."""
    from bankstatement.values import amount

    with pytest.raises(ValueError):
        amount(raw)


@pytest.mark.parametrize("raw", ["1.840,50", "12.50", "100,500", Decimal("7.100"), 12.5])
def test_trailing_zeros_and_thousands_are_not_extra_decimals(raw):
    from bankstatement.values import amount

    assert amount(raw) is not None


@pytest.mark.parametrize("raw", ["01.01.1999", "9999-12-31", "01.06.2101"])
def test_a_date_outside_any_statement_is_refused(raw):
    """Год 9999 завёл бы учётный месяц через восемь тысяч лет."""
    from bankstatement.values import day

    with pytest.raises(ValueError):
        day(raw)


def _book_bytes() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    book.active.append(["Datum", "Duguje"])
    book.active.append([date(2026, 6, 8), 5900])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _with_part(content: bytes, name: str, replace) -> bytes:
    """Та же книга, но одна её часть заменена — так, как её прислали бы."""
    import zipfile

    source = zipfile.ZipFile(io.BytesIO(content))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            target.writestr(item.filename, replace(data) if item.filename == name else data)
    return out.getvalue()


def test_a_sheet_with_a_doctype_is_refused():
    doctype = b'<!DOCTYPE worksheet [<!ENTITY x "y">]>'

    def declared(data: bytes) -> bytes:
        if data.startswith(b"<?xml"):
            end = data.index(b"?>") + 2
            return data[:end] + doctype + data[end:]
        return doctype + data

    probe = _with_part(_book_bytes(), "xl/worksheets/sheet1.xml", declared)
    import zipfile
    assert doctype in zipfile.ZipFile(io.BytesIO(probe)).read("xl/worksheets/sheet1.xml")

    book = _with_part(_book_bytes(), "xl/worksheets/sheet1.xml", declared)
    with pytest.raises(Unreadable):
        read(book, "izvod.xlsx")


def test_a_broken_sheet_is_refused_in_words():
    book = _with_part(_book_bytes(), "xl/worksheets/sheet1.xml", lambda data: data[:120])
    with pytest.raises(Unreadable):
        read(book, "izvod.xlsx")


def test_an_absurdly_long_csv_field_is_refused_in_words():
    huge = "x" * 200_000
    with pytest.raises(Unreadable):
        read((CSV + f'"{huge}";1,00;;18.06.2026;Neko\n').encode("utf-8"), "izvod.csv")


def _renamed_sheet(content: bytes, new_name: str, replace=lambda data: data) -> bytes:
    """Та же книга, но часть листа лежит под другим именем, и книга на него ссылается."""
    import zipfile

    old = "xl/worksheets/sheet1.xml"
    source = zipfile.ZipFile(io.BytesIO(content))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == old:
                target.writestr(new_name, replace(data))
                continue
            short_old, short_new = old.removeprefix("xl/"), new_name.removeprefix("xl/")
            data = data.replace(old.encode(), new_name.encode())
            data = data.replace(short_old.encode(), short_new.encode())
            target.writestr(item.filename, data)
    return out.getvalue()


@pytest.mark.parametrize("name", ["xl/worksheets/SHEET1.XML", "xl/worksheets/sheet1.dat"])
def test_a_sheet_under_another_name_is_checked_too(name):
    doctype = b'<!DOCTYPE worksheet [<!ENTITY x "y">]>'

    def declared(data: bytes) -> bytes:
        end = data.index(b"?>") + 2 if data.startswith(b"<?xml") else 0
        return data[:end] + doctype + data[end:]

    # Под новым именем книга по-прежнему открывается: лист берут по ссылке.
    clean = _renamed_sheet(_book_bytes(), name)
    assert read(clean, "izvod.xlsx").lines

    book = _renamed_sheet(_book_bytes(), name, declared)
    import zipfile
    assert doctype in zipfile.ZipFile(io.BytesIO(book)).read(name)
    with pytest.raises(Unreadable):
        read(book, "izvod.xlsx")


def _with_extra_part(content: bytes, info, data: bytes) -> bytes:
    """Та же книга плюс ещё одна часть архива."""
    import zipfile

    source = zipfile.ZipFile(io.BytesIO(content))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item.filename, source.read(item.filename))
        target.writestr(info, data)
    return out.getvalue()


HOSTILE_PART = '<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'


def test_a_utf16_part_without_bom_is_checked():
    data = HOSTILE_PART.encode("utf-16-be")
    assert not data.startswith((b"\xff\xfe", b"\xfe\xff"))
    book = _with_extra_part(_book_bytes(), "xl/custom.bin", data)
    with pytest.raises(Unreadable):
        read(book, "izvod.xlsx")


def test_a_part_marked_as_folder_is_checked_when_it_has_content():
    import zipfile

    book = _with_extra_part(_book_bytes(), zipfile.ZipInfo("xl/odd/"), HOSTILE_PART.encode())
    assert zipfile.ZipFile(io.BytesIO(book)).getinfo("xl/odd/").is_dir()
    with pytest.raises(Unreadable):
        read(book, "izvod.xlsx")


def test_a_part_that_is_neither_xml_nor_known_binary_is_refused():
    book = _with_extra_part(_book_bytes(), "xl/blob.bin", b"\x00\x13garbage")
    with pytest.raises(Unreadable):
        read(book, "izvod.xlsx")


def test_known_binary_parts_do_not_stop_a_book():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    book = _with_extra_part(_book_bytes(), "xl/media/image1.png", png)
    book = _with_extra_part(book, "xl/vbaProject.bin", b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    assert read(book, "izvod.xlsx").lines


def test_a_book_with_too_many_parts_is_refused():
    import zipfile

    from bankstatement.reader import MAX_PARTS
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for number in range(MAX_PARTS + 1):
            target.writestr(f"p{number}.xml", "<x/>")
    with pytest.raises(Unreadable, match=str(MAX_PARTS)):
        read(out.getvalue(), "izvod.xlsx")


@pytest.mark.parametrize("error", [EOFError, NotImplementedError, __import__("zlib").error])
def test_an_archive_that_breaks_while_unpacking_is_refused_in_words(monkeypatch, error):
    import zipfile

    def broken(self, name, *args, **kwargs):
        raise error("broken")

    monkeypatch.setattr(zipfile.ZipFile, "read", broken)
    with pytest.raises(Unreadable):
        read(_book_bytes(), "izvod.xlsx")
