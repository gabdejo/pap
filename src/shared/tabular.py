# src/shared/tabular.py
# ---------------------------------------------------------------
# Tolerant readers for user-supplied tabular files (Excel or CSV),
# ported from the SPP monitor. Files uploaded from a browser arrive
# in every dialect Excel can produce, and guessing wrong about the
# decimal separator multiplies every value by a thousand in silence
# - so the decimal style is decided by looking at the numbers, never
# at the column delimiter.
#
# Format is recognized by CONTENT, not extension: the SBS publishes
# .xls files that are xlsx inside.
# ---------------------------------------------------------------

import csv as _csv
import datetime as dt
import io
import re
import unicodedata

import pandas as pd


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(text))
                   if unicodedata.category(c) != "Mn")


def column_key(text) -> str:
    """
    Normalized header cell: no accents, no spaces or signs, lowercase.

    :param text: Raw header cell
    :return: Folded key for alias comparison
    :rtype: str
    """
    return re.sub(r"[^a-z0-9]", "", strip_accents(str(text or "")).lower())


def is_excel(data) -> bool:
    """
    Recognizes an Excel file by content (zip or OLE2 signature).

    :param data: File content
    :type data: bytes
    :rtype: bool
    """
    if not isinstance(data, bytes):
        return False
    return data[:4] == b"PK\x03\x04" or data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def rows_from_excel(content: bytes, sheet=None) -> tuple:
    """
    Raw cells of one sheet, without assuming where the table starts.

    :return: (rows as list of lists, sheet name, all sheet names)
    :rtype: tuple
    :raises ValueError: If the content cannot be opened as Excel
    """
    try:
        book = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
    except Exception as exc:
        raise ValueError(
            "The file could not be opened as Excel. If it is a legacy .xls, "
            "open it in Excel and save it as .xlsx.") from exc
    name = sheet or book.sheet_names[0]
    if name not in book.sheet_names:
        raise ValueError(f"The file has no sheet named '{name}'. "
                         f"It has: {', '.join(book.sheet_names)}.")
    raw = pd.read_excel(book, sheet_name=name, header=None)
    return raw.values.tolist(), name, list(book.sheet_names)


def rows_from_csv(data) -> tuple:
    """
    Raw cells of a CSV, with delimiter and decimal style deduced from
    the file itself.

    :return: (rows, delimiter, True when comma is the decimal mark)
    :rtype: tuple
    :raises ValueError: On unknown encoding or an empty file
    """
    if isinstance(data, bytes):
        text = None
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("The file could not be read: unknown encoding.")
    else:
        text = str(data)
    text = text.lstrip("﻿")

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("The file has no data: a header and at least one row "
                         "were expected.")
    sep, comma_decimal = _dialect(lines)
    return list(_csv.reader(lines, delimiter=sep)), sep, comma_decimal


def _dialect(lines: list) -> tuple:
    sample = "\n".join(lines[:30])
    sep = max([";", ",", "\t", "|"], key=lambda c: sample.count(c))
    if sample.count(sep) == 0:
        sep = ","

    cells = []
    try:
        for row in list(_csv.reader(lines[1:30], delimiter=sep)):
            for c in row:
                c = str(c).replace("\xa0", " ").strip()
                if c and re.fullmatch(r"-?[\d.,\s]+", c) and re.search(r"\d", c):
                    cells.append(c)
    except _csv.Error:
        cells = []

    style = decimal_style(cells)
    if style is not None:
        return sep, style
    # No evidence in the numbers: with ';' as delimiter, comma-decimal
    # is the most likely dialect.
    return sep, sep == ";"


def decimal_style(cells: list) -> bool | None:
    """
    Whether a sample of numeric strings uses comma or point as the
    decimal mark.

    :param cells: Strings that look numeric
    :type cells: list
    :return: True comma-decimal, False point-decimal, None undecidable
    :rtype: bool | None
    """
    comma = point = 0
    for c in cells:
        c = str(c).strip()
        has_point, has_comma = "." in c, "," in c
        if has_point and has_comma:
            if c.rfind(",") > c.rfind("."):
                comma += 1
            else:
                point += 1
        elif has_comma:
            groups = c.split(",")
            if len(groups) == 2 and len(groups[1]) != 3:
                comma += 1
        elif has_point:
            groups = c.split(".")
            if len(groups) == 2 and len(groups[1]) != 3:
                point += 1
    if not (comma or point):
        return None
    return comma > point


def header_row(rows: list, is_header, limit: int = 12) -> int | None:
    """
    Index of the first row satisfying `is_header(cell)` for any cell,
    or None. Hand-made spreadsheets bring titles, cut-off dates or blank
    lines before the headers, so the table is FOUND, never assumed to
    start at row one.

    :param is_header: Predicate over a raw cell value
    :rtype: int | None
    """
    for i, row in enumerate(rows[:limit]):
        if any(is_header(c) for c in row):
            return i
    return None


def clean_header(row: list) -> list:
    """Header cells as stripped strings, NaN/None as empty."""
    return ["" if c is None or (isinstance(c, float) and c != c)
            else str(c).strip() for c in row]


def parse_date(text) -> dt.date | None:
    """
    A date from any reasonable form: date/datetime objects, Excel
    serial numbers, ISO or day-first text.

    :return: date or None when unreadable
    :rtype: datetime.date | None
    """
    if isinstance(text, dt.datetime):
        return text.date()
    if isinstance(text, dt.date):
        return text
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        if text != text:                       # NaN
            return None
        # Excel serial, counted from 1899-12-30; bounded to a sensible
        # range so a stray number is not mistaken for a date.
        if 15000 <= float(text) <= 60000:
            return dt.date(1899, 12, 30) + dt.timedelta(days=int(text))
        return None
    t = str(text or "").strip()
    if not t:
        return None
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", t)
    if m:
        year, month, day = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", t)
        if not m:
            return None
        day, month, year = (int(x) for x in m.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def parse_number(text, comma_decimal: bool) -> float | None:
    """
    A number written Peruvian-style or English-style. Typed values
    (the normal case coming from Excel) pass through unchanged.

    :param comma_decimal: True when comma is the decimal mark
    :rtype: float | None
    """
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return None if text != text else float(text)
    t = str(text if text is not None else "")
    # Both non-breaking spaces Excel/es-locale emit as thousands
    # separators: U+202F (narrow) and U+00A0 (plain NBSP). Missing one
    # made every value >= 1000 fail float() and vanish as an empty cell.
    for space in (" ", " "):
        t = t.replace(space, " ")
    t = t.strip()
    if t in ("", "-", "--", "n.d.", "N.D.", "NA", "#N/A"):
        return None
    if comma_decimal:
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    t = t.replace(" ", "")
    try:
        return float(t)
    except ValueError:
        return None
