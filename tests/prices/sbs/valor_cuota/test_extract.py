# tests/prices/sbs/valor_cuota/test_extract.py
# ---------------------------------------------------------------
# Pins down the extract helpers. No DB, no SBS.
#
#   - _num treats NaN (an empty Excel cell) as "no value": letting it
#     through would create fact rows with NaN prices (regression from
#     the phase-2 review flow)
#   - parse_historico reads the SBS layout (merged fund groups on row
#     3, AFP names on row 4) into long rows, drops empty cells, and
#     accumulates unknown AFPs instead of raising
# ---------------------------------------------------------------

import datetime as dt
import io

from openpyxl import Workbook

from src.pipelines.prices.sbs.valor_cuota.extract import _num, parse_historico


def test_num_nan_is_none():
    assert _num(float("nan")) is None


def test_num_accepts_typed_and_text():
    assert _num(25.5) == 25.5
    assert _num("1,234.5") == 1234.5
    assert _num("n.d.") is None
    assert _num("") is None
    assert _num(True) is None


def _xls(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Valor cuota diario"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SBS_ROWS = [
    ["titulo"],
    [None],
    [None, "Fondo Tipo 2", None],        # row 3: merged group headers
    [None, "PROFUTURO", "AFP DESCONOCIDA"],
    [dt.date(2026, 8, 27), 277.4, 99.9],
    [dt.date(2026, 8, 28), 277.9, None],  # empty cell -> no row
]


def test_parse_historico_long_rows_and_empty_cells():
    df = parse_historico(_xls(SBS_ROWS))
    # Only PROFUTURO resolves; the empty cell on the 28th produces no row.
    assert len(df) == 2
    assert set(df["afp"]) == {"profuturo"}
    assert set(df["fondo"]) == {2}
    assert df.set_index("date").at[dt.date(2026, 8, 27), "valor_cuota"] == 277.4


def test_parse_historico_accumulates_unknown_afps():
    vistas: set = set()
    parse_historico(_xls(SBS_ROWS), vistas)
    assert vistas == {"AFP DESCONOCIDA"}


def test_parse_historico_text_dates_are_dayfirst():
    # A re-saved workbook can bring the date column as dd/mm TEXT: without
    # dayfirst, '05/08/1993' would silently land on May 8.
    rows = [r[:] for r in SBS_ROWS[:4]] + [["05/08/1993", 10.0, None]]
    df = parse_historico(_xls(rows))
    assert list(df["date"]) == [dt.date(1993, 8, 5)]
