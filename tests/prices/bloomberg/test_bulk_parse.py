# tests/prices/bloomberg/test_bulk_parse.py
# ---------------------------------------------------------------
# bulk_to_df: a bulk reference field (EQY_DVD_ADJUST_FACT) must parse to
# the same long shape whether a security returns 0, 1 or many rows. The
# generic secdata_to_df returns three different shapes for those cases.
# ---------------------------------------------------------------

import sys
from datetime import date
from unittest.mock import MagicMock

# blpapi only exists on the workstation; see test_extract_groups.py.
sys.modules.setdefault("blpapi", MagicMock())

from src.vendors.bloomberg import bulk_to_df  # noqa: E402

FIELD = "EQY_DVD_ADJUST_FACT"


def _row(d: date, factor: float) -> dict:
    # Sub-field names as the Terminal returns them (verified 2026-11-26).
    return {"Adjustment Date": d, "Adjustment Factor": factor,
            "Operator Type": 2.0, "Flag": 1.0}


def _sec(ticker: str, rows: list[dict] | None) -> dict:
    field_data = {} if rows is None else {FIELD: rows}
    return {"security": ticker, "eidData": [], "fieldExceptions": [], "fieldData": field_data}


def test_zero_one_and_many_rows_share_one_shape():
    # Two messages, as a partial + final response would arrive.
    to_pys = [
        {"securityData": [_sec("NONE US Equity", None),
                          _sec("ONE US Equity", [_row(date(2024, 5, 17), 0.95)])]},
        {"securityData": [_sec("MANY US Equity", [_row(date(2023, 5, 18), 0.95),
                                                  _row(date(2024, 5, 17), 0.94)])]},
    ]
    df = bulk_to_df(to_pys, FIELD)

    assert list(df["id"]) == ["ONE US Equity", "MANY US Equity", "MANY US Equity"]
    assert list(df.columns) == ["id", "Adjustment Date", "Adjustment Factor",
                                "Operator Type", "Flag"]
    assert list(df["Adjustment Factor"]) == [0.95, 0.95, 0.94]


def test_no_rows_anywhere_is_an_empty_frame():
    assert bulk_to_df([{"securityData": [_sec("NONE US Equity", None)]}], FIELD).empty
