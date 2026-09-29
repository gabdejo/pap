# tests/prices/sbs/valor_cuota/test_scheduled.py
# ---------------------------------------------------------------
# The scheduled run: which day it expects to find published.
#
# The SBS publishes the valor cuota with a two-business-day lag. The
# Windows task fires at 16:00 and retries at 16:30 and 17:00; before
# opening Chrome every attempt asks whether the book already holds
# that day. The question starts with knowing the day: pinned here.
# No DB.
# ---------------------------------------------------------------

import datetime as dt

from src.pipelines.prices.sbs.valor_cuota.run import expected_date


def test_midweek_is_two_business_days_back():
    # Wednesday 2026-09-23 -> Monday 2026-09-21
    assert expected_date(dt.date(2026, 9, 23)) == dt.date(2026, 9, 21)


def test_monday_and_tuesday_skip_the_weekend():
    # Monday 09-21 -> Thursday 09-17; Tuesday 09-22 -> Friday 09-18
    assert expected_date(dt.date(2026, 9, 21)) == dt.date(2026, 9, 17)
    assert expected_date(dt.date(2026, 9, 22)) == dt.date(2026, 9, 18)


def test_weekend_counts_from_friday():
    # Saturday 09-26 -> Thursday 09-24 (Friday and Thursday are the two
    # business days back)
    assert expected_date(dt.date(2026, 9, 26)) == dt.date(2026, 9, 24)
