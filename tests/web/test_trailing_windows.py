# tests/web/test_trailing_windows.py
# ---------------------------------------------------------------
# The trailing windows of the return tables (3M, 6M, 12M and the
# annualized 3A, 5A, 10A): where each one is based on the closing grid
# and how the annualized rate is computed. No DB.
# ---------------------------------------------------------------

import datetime as dt

import pytest

from web.api.services.spp import (ANNUALIZED_YEARS, RETURN_KEYS, _annualized,
                                  _months_back, reference_dates)


def _business_days(start: dt.date, end: dt.date) -> list:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def test_trailing_bases_are_the_close_on_or_before_the_same_day_back():
    dates = _business_days(dt.date(2015, 1, 1), dt.date(2026, 10, 19))
    ref = reference_dates(dates, dt.date(2026, 10, 19))      # a Monday
    assert ref["m3"] == dt.date(2026, 7, 17)     # 19/07 is a Sunday -> Friday 17th
    assert ref["m6"] == dt.date(2026, 4, 17)     # 19/04 is a Sunday
    assert ref["m12"] == dt.date(2025, 10, 17)   # 19/10/2025 is a Sunday
    assert ref["a3"] == dt.date(2023, 10, 19)    # a Thursday: the day itself
    assert ref["a5"] == dt.date(2021, 10, 19)
    assert ref["a10"] == dt.date(2016, 10, 19)


def test_month_arithmetic_clamps_to_the_month_end():
    assert _months_back(dt.date(2026, 5, 31), 3) == dt.date(2026, 2, 28)
    assert _months_back(dt.date(2028, 5, 31), 3) == dt.date(2028, 2, 29)
    assert _months_back(dt.date(2028, 2, 29), 36) == dt.date(2025, 2, 28)
    assert _months_back(dt.date(2026, 1, 15), 12) == dt.date(2025, 1, 15)


def test_without_enough_history_the_trailing_base_is_none():
    dates = _business_days(dt.date(2024, 1, 1), dt.date(2026, 10, 19))
    ref = reference_dates(dates, dt.date(2026, 10, 19))
    assert ref["m12"] == dt.date(2025, 10, 17)
    assert ref["a3"] is None
    assert ref["a10"] is None


def test_annualized_is_the_compound_rate_per_year():
    assert _annualized(1.331, 1.0, 3) == pytest.approx(0.10)
    assert _annualized(2.0, 1.0, 10) == pytest.approx(2 ** 0.1 - 1)
    assert _annualized(0.9, 1.0, 5) == pytest.approx(0.9 ** 0.2 - 1)
    assert _annualized(None, 1.0, 3) is None
    assert _annualized(1.1, None, 3) is None
    assert _annualized(1.1, 0.0, 3) is None


def test_return_columns_run_newest_first_with_one_unit_change():
    keys = [k for k, _, _ in RETURN_KEYS]
    units = {k: u for k, _, u in RETURN_KEYS}
    for k in ("a10", "a5", "a3", "m12", "m6", "m3"):
        assert k in keys
    # Up to 6M in basis points, from YTD on in percent: one unit change.
    assert all(units[k] == "bps" for k in ("d0", "d1", "d2", "d3", "d5", "mtd", "m1", "m2", "m3", "m6"))
    assert all(units[k] == "pct" for k in ("ytd", "fy", "m12", "a1", "a3", "a5", "a10"))
    changes = sum(1 for a, b in zip(keys, keys[1:]) if units[a] != units[b] and "level" not in (a, b))
    assert changes == 1
    # Newest start on the left: today, the month, the quarter, the year,
    # the decade, and the level last.
    assert keys.index("d0") < keys.index("mtd") < keys.index("m3") < keys.index("m6")
    assert keys.index("m6") < keys.index("ytd") < keys.index("a1") < keys.index("a10") < keys.index("level")
    assert set(ANNUALIZED_YEARS) == {"a3", "a5", "a10"}
