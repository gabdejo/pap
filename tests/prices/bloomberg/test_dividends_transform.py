# tests/prices/bloomberg/test_dividends_transform.py
# ---------------------------------------------------------------
# Pure parts of the Bloomberg adjustment-factor pipeline: the vendor's
# operator folded into one multiplicative convention, same-day events
# collapsed, additive factors refused, and the universe filter.
# ---------------------------------------------------------------

import sys
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

# blpapi only exists on the workstation; see test_extract_groups.py.
sys.modules.setdefault("blpapi", MagicMock())

from src.pipelines.prices.bloomberg.dividends.extract import COLUMNS, _normalize  # noqa: E402
from src.pipelines.prices.bloomberg.dividends.run import _universe  # noqa: E402
from src.pipelines.prices.bloomberg.dividends.transform import transform_factors  # noqa: E402

D1, D2 = date(2024, 5, 17), date(2024, 9, 23)


def _raw(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def test_operators_fold_into_multiply_prior_prices():
    raw = _raw([
        ["A US Equity", "CAPITAL_CHANGE", D1, 2.0, 1, 3],     # 2:1 split: divide by 2
        ["A US Equity", "NORMAL_CASH", D2, 0.98, 2, 1],       # dividend: multiply by 0.98
    ])
    out = transform_factors(raw, {"A US Equity": 7})

    got = {(r.entity_id, r.date, r.action_type): r.factor for r in out.itertuples()}
    assert got == {(7, D1, "CAPITAL_CHANGE"): 0.5, (7, D2, "NORMAL_CASH"): 0.98}
    assert set(out["source"]) == {"bloomberg"}


def test_same_day_same_type_events_multiply():
    raw = _raw([
        ["A US Equity", "NORMAL_CASH", D1, 0.9, 2, 1],
        ["A US Equity", "NORMAL_CASH", D1, 0.5, 2, 1],
        ["A US Equity", "ABNORMAL_CASH", D1, 0.8, 2, 1],      # other type: its own row
    ])
    out = transform_factors(raw, {"A US Equity": 7})

    got = {r.action_type: r.factor for r in out.itertuples()}
    assert got == {"NORMAL_CASH": pytest.approx(0.45), "ABNORMAL_CASH": 0.8}


def test_additive_bad_and_unmapped_rows_are_dropped():
    raw = _raw([
        ["A US Equity", "CAPITAL_CHANGE", D1, 1.5, 3, 1],     # additive: no multiplicative form
        ["A US Equity", "NORMAL_CASH", D1, 0.0, 2, 1],        # non-positive
        ["Z US Equity", "NORMAL_CASH", D1, 0.9, 2, 1],        # not in the universe
        ["A US Equity", "NORMAL_CASH", D2, 0.9, 2, 1],
    ])
    out = transform_factors(raw, {"A US Equity": 7})

    assert [(r.date, r.factor) for r in out.itertuples()] == [(D2, 0.9)]


def test_normalize_maps_terminal_sub_fields():
    bulk = pd.DataFrame([{
        "id": "A US Equity", "Adjustment Date": D1, "Adjustment Factor": 0.95,
        "Operator Type": 2.0, "Flag": 1.0,
    }])
    out = _normalize(bulk, "NORMAL_CASH")

    assert out.to_dict("records") == [{
        "parsekyable": "A US Equity", "action_filter": "NORMAL_CASH", "date": D1,
        "factor": 0.95, "operator_type": 2, "flag": 1,
    }]
    with pytest.raises(ValueError):
        _normalize(bulk.drop(columns="Flag"), "NORMAL_CASH")


def test_universe_is_equity_px_last_only():
    series = [
        {"parsekyable": "A US Equity", "field": "PX_LAST", "entity_id": 1},
        {"parsekyable": "A US Equity", "field": "PX_VOLUME", "entity_id": 1},
        {"parsekyable": "SPX Index", "field": "PX_LAST", "entity_id": 2},
        {"parsekyable": None, "field": "PX_LAST", "entity_id": 3},
        {"parsekyable": "NBCUI2D ID Equity", "field": "PX_LAST", "entity_id": 4},
    ]
    assert _universe(series, None) == {"A US Equity": 1, "NBCUI2D ID Equity": 4}
    assert _universe(series, ["a us equity"]) == {"A US Equity": 1}
