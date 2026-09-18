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


def sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t))
                   if unicodedata.category(c) != "Mn")


def clave_col(texto) -> str:
    """
    Normalized header cell: no accents, no spaces or signs, lowercase.

    :param texto: Raw header cell
    :return: Folded key for alias comparison
    :rtype: str
    """
    return re.sub(r"[^a-z0-9]", "", sin_tildes(str(texto or "")).lower())


def es_excel(datos) -> bool:
    """
    Recognizes an Excel file by content (zip or OLE2 signature).

    :param datos: File content
    :type datos: bytes
    :rtype: bool
    """
    if not isinstance(datos, bytes):
        return False
    return datos[:4] == b"PK\x03\x04" or datos[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def filas_de_excel(contenido: bytes, hoja=None) -> tuple:
    """
    Raw cells of one sheet, without assuming where the table starts.

    :return: (rows as list of lists, sheet name, all sheet names)
    :rtype: tuple
    :raises ValueError: If the content cannot be opened as Excel
    """
    try:
        libro = pd.ExcelFile(io.BytesIO(contenido), engine="openpyxl")
    except Exception as exc:
        raise ValueError(
            "No se pudo abrir el archivo como Excel. Si es un .xls antiguo, "
            "abrelo en Excel y guardalo como .xlsx.") from exc
    nombre = hoja or libro.sheet_names[0]
    if nombre not in libro.sheet_names:
        raise ValueError(f"El archivo no tiene la hoja '{nombre}'. "
                         f"Tiene: {', '.join(libro.sheet_names)}.")
    crudo = pd.read_excel(libro, sheet_name=nombre, header=None)
    return crudo.values.tolist(), nombre, list(libro.sheet_names)


def filas_de_csv(datos) -> tuple:
    """
    Raw cells of a CSV, with delimiter and decimal style deduced from
    the file itself.

    :return: (rows, delimiter, True when comma is the decimal mark)
    :rtype: tuple
    :raises ValueError: On unknown encoding or an empty file
    """
    if isinstance(datos, bytes):
        texto = None
        for cod in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                texto = datos.decode(cod)
                break
            except UnicodeDecodeError:
                continue
        if texto is None:
            raise ValueError("No se pudo leer el archivo: codificacion desconocida.")
    else:
        texto = str(datos)
    texto = texto.lstrip("﻿")

    lineas = [l for l in texto.splitlines() if l.strip()]
    if len(lineas) < 2:
        raise ValueError("El archivo no tiene datos: se esperaba una cabecera "
                         "y al menos una fila.")
    sep, coma_decimal = _dialecto(lineas)
    return list(_csv.reader(lineas, delimiter=sep)), sep, coma_decimal


def _dialecto(lineas: list) -> tuple:
    muestra = "\n".join(lineas[:30])
    sep = max([";", ",", "\t", "|"], key=lambda c: muestra.count(c))
    if muestra.count(sep) == 0:
        sep = ","

    celdas = []
    try:
        for fila in list(_csv.reader(lineas[1:30], delimiter=sep)):
            for c in fila:
                c = str(c).replace("\xa0", " ").strip()
                if c and re.fullmatch(r"-?[\d.,\s]+", c) and re.search(r"\d", c):
                    celdas.append(c)
    except Exception:
        celdas = []

    estilo = estilo_decimal(celdas)
    if estilo is not None:
        return sep, estilo
    # No evidence in the numbers: with ';' as delimiter, comma-decimal
    # is the most likely dialect.
    return sep, sep == ";"


def estilo_decimal(celdas: list):
    """
    Whether a sample of numeric strings uses comma or point as the
    decimal mark.

    :param celdas: Strings that look numeric
    :type celdas: list
    :return: True comma-decimal, False point-decimal, None undecidable
    :rtype: bool | None
    """
    coma = punto = 0
    for c in celdas:
        c = str(c).strip()
        tiene_p, tiene_c = "." in c, "," in c
        if tiene_p and tiene_c:
            if c.rfind(",") > c.rfind("."):
                coma += 1
            else:
                punto += 1
        elif tiene_c:
            grupos = c.split(",")
            if len(grupos) == 2 and len(grupos[1]) != 3:
                coma += 1
        elif tiene_p:
            grupos = c.split(".")
            if len(grupos) == 2 and len(grupos[1]) != 3:
                punto += 1
    if not (coma or punto):
        return None
    return coma > punto


def fila_cabecera(filas: list, es_cabecera, limite: int = 12):
    """
    Index of the first row satisfying `es_cabecera(celda)` for any cell,
    or None. Hand-made spreadsheets bring titles, cut-off dates or blank
    lines before the headers, so the table is FOUND, never assumed to
    start at row one.

    :param es_cabecera: Predicate over a raw cell value
    :rtype: int | None
    """
    for i, fila in enumerate(filas[:limite]):
        if any(es_cabecera(c) for c in fila):
            return i
    return None


def limpiar_cabecera(fila: list) -> list:
    """Header cells as stripped strings, NaN/None as empty."""
    return ["" if c is None or (isinstance(c, float) and c != c)
            else str(c).strip() for c in fila]


def fecha_flexible(texto):
    """
    A date from any reasonable form: date/datetime objects, Excel
    serial numbers, ISO or day-first text.

    :return: date or None when unreadable
    :rtype: datetime.date | None
    """
    if isinstance(texto, dt.datetime):
        return texto.date()
    if isinstance(texto, dt.date):
        return texto
    if isinstance(texto, (int, float)) and not isinstance(texto, bool):
        if texto != texto:                     # NaN
            return None
        # Excel serial, counted from 1899-12-30; bounded to a sensible
        # range so a stray number is not mistaken for a date.
        if 15000 <= float(texto) <= 60000:
            return dt.date(1899, 12, 30) + dt.timedelta(days=int(texto))
        return None
    t = str(texto or "").strip()
    if not t:
        return None
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", t)
    if m:
        a, me, d = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", t)
        if not m:
            return None
        d, me, a = (int(x) for x in m.groups())
    try:
        return dt.date(a, me, d)
    except ValueError:
        return None


def num_flexible(texto, coma_decimal):
    """
    A number written Peruvian-style or English-style. Typed values
    (the normal case coming from Excel) pass through unchanged.

    :param coma_decimal: True when comma is the decimal mark
    :rtype: float | None
    """
    if isinstance(texto, bool):
        return None
    if isinstance(texto, (int, float)):
        return None if texto != texto else float(texto)
    t = str(texto if texto is not None else "")
    # Both non-breaking spaces Excel/es-locale emit as thousands
    # separators: U+202F (narrow) and U+00A0 (plain NBSP). Missing one
    # made every value >= 1000 fail float() and vanish as an empty cell.
    for espacio in (" ", " "):
        t = t.replace(espacio, " ")
    t = t.strip()
    if t in ("", "-", "--", "n.d.", "N.D.", "NA", "#N/A"):
        return None
    if coma_decimal:
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    t = t.replace(" ", "")
    try:
        return float(t)
    except ValueError:
        return None
