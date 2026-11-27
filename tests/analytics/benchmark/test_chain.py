# tests/analytics/benchmark/test_chain.py
# ---------------------------------------------------------------
# Golden tests for chain() / chain_levels(): the drifting
# (buy-and-hold) composite-index chaining, and the fixed-weights
# expansion around it. No DB.
#
# Pinned down:
#   - units are struck at the effective date (weight * level / price)
#     and the index is their mark-to-market afterwards, so weights DRIFT
#   - a new version re-strikes at the chained level: the index is
#     CONTINUOUS across composition changes, whatever the price scale
#     of the constituents that come and go
#   - prices forward-fill inside the grid
#   - a constituent with no price at the strike fails loud, by name
#   - holdings are end-of-day: a handover date carries the new basket
#   - fixed weights re-strike on calendar boundaries
#   - total return on RAW prices: a constituent's units are divided by
#     its adjustment factors as ex-dates are crossed, so a split is not
#     a loss, a dividend is reinvested, and no row depends on a factor
#     dated after it
# ---------------------------------------------------------------

import datetime as dt

import pytest

from src.pipelines.analytics.benchmark.materialize import (
    chain, chain_levels, expand_fixed_weights, rebalance_dates,
)

D1, D2, D3, D4 = (dt.date(2026, 1, d) for d in (5, 6, 7, 8))


def _c(label, weight, prices):
    return {"label": label, "weight": weight, "prices": prices}


def test_one_period_drifts():
    # uA = 0.6*100/10 = 6 units; uB = 0.4*100/20 = 2 units.
    levels = dict(chain_levels([{
        "from": D1,
        "constituents": [
            _c("A", 0.6, {D1: 10, D2: 12, D3: 11}),
            _c("B", 0.4, {D1: 20, D2: 20, D3: 22}),
        ],
    }]))
    assert levels[D1] == pytest.approx(100.0)
    assert levels[D2] == pytest.approx(6 * 12 + 2 * 20)   # 112: A now weighs more
    assert levels[D3] == pytest.approx(6 * 11 + 2 * 22)   # 110


def test_rebalance_is_continuous_and_re_strikes_weights():
    # Every constituent arrives with its WHOLE series (that is how
    # build_periods hands them over), not trimmed to the period: that is
    # why the old basket can be marked on the handover date.
    A = {D1: 10, D2: 12, D3: 11, D4: 11}
    B = {D1: 20, D2: 20, D3: 22, D4: 24.2}
    levels = dict(chain_levels([
        {"from": D1, "constituents": [_c("A", 0.6, A), _c("B", 0.4, B)]},
        {"from": D3, "constituents": [_c("A", 0.5, A), _c("B", 0.5, B)]},
    ]))
    assert levels[D2] == pytest.approx(112.0)
    # D3 is the handover date and BELONGS TO THE OLD BASKET: it is marked
    # with it (A 12->11, B 20->22 on 6 and 2 units = 110). The rebalance
    # does not move that level, it only splits 110 fifty-fifty; the new
    # basket governs from the next close.
    assert levels[D3] == pytest.approx(6 * 11 + 2 * 22)      # 110
    # D4: uA = 0.5*110/11 = 5 and uB = 0.5*110/22 = 2.5; B up 10%.
    assert levels[D4] == pytest.approx(5 * 11 + 2.5 * 24.2)  # 115.5


def test_missing_price_forward_fills():
    levels = dict(chain_levels([{
        "from": D1,
        "constituents": [
            _c("A", 0.5, {D1: 10, D2: 12}),
            _c("B", 0.5, {D1: 20}),             # no price on D2: ffill
        ],
    }]))
    assert levels[D2] == pytest.approx(5 * 12 + 2.5 * 20)  # B at its last price


def test_no_price_at_strike_fails_by_name():
    with pytest.raises(ValueError, match="'B'"):
        chain_levels([{
            "from": D1,
            "constituents": [
                _c("A", 0.5, {D1: 10}),
                _c("B", 0.5, {D2: 20}),         # its first price comes LATER
            ],
        }])


def test_base_is_configurable():
    levels = dict(chain_levels(
        [{"from": D1, "constituents": [_c("A", 1.0, {D1: 10, D2: 11})]}],
        base=1000.0))
    assert levels[D1] == pytest.approx(1000.0)
    assert levels[D2] == pytest.approx(1100.0)


def test_swapping_a_constituent_does_not_jump():
    """
    Replacing a constituent by one of a COMPLETELY different price scale
    cannot move the index on the day of the change. That is what makes
    a real benchmark usable, where tickers are discontinued and
    replaced: what changes is what the index represents from then on,
    not its level. Chained on prices instead of re-struck units, going
    from a 50 ticker to a 7,500 one would multiply the series by 150 in
    a day.
    """
    levels = dict(chain_levels([
        {"from": D1, "constituents": [
            _c("A", 0.5, {D1: 100.0, D2: 110.0, D3: 120.0}),
            _c("B", 0.5, {D1: 50.0, D2: 50.0, D3: 50.0}),
        ]},
        {"from": D3, "constituents": [
            _c("A", 0.7, {D3: 120.0, D4: 132.0}),
            _c("C", 0.3, {D3: 7_500.0, D4: 7_500.0}),   # replaces B
        ]},
    ]))
    # D2: A +10% and B flat, on 0.5 and 1 units -> 105
    assert levels[D2] == pytest.approx(105.0)
    # D3 is the day of the change: marked with the OLD basket (110), and
    # swapping B for C - 50 to 7,500 - does not move the level.
    assert levels[D3] == pytest.approx(0.5 * 120 + 1 * 50)   # 110
    # D4: on the new basket, A +10% weighing 70% -> +7%
    assert levels[D4] == pytest.approx(110.0 * 1.07)


def test_changing_only_the_weights_does_not_jump_either():
    levels = dict(chain_levels([
        {"from": D1, "constituents": [
            _c("A", 0.5, {D1: 100.0, D2: 120.0}),
            _c("B", 0.5, {D1: 100.0, D2: 100.0}),
        ]},
        {"from": D2, "constituents": [
            _c("A", 0.9, {D2: 120.0, D3: 132.0}),
            _c("B", 0.1, {D2: 100.0, D3: 100.0}),
        ]},
    ]))
    assert levels[D2] == pytest.approx(110.0)     # +20% and 0%, fifty-fifty
    # The D2 rebalance does not move the level; D3 is the first day at 90/10.
    assert levels[D3] == pytest.approx(110.0 * (1 + 0.9 * 0.10))


def test_holdings_are_end_of_day_and_sum_to_the_level():
    A = {D1: 10, D2: 12, D3: 11, D4: 11}
    B = {D1: 20, D2: 20, D3: 22, D4: 24.2}
    rows = {r["date"]: r for r in chain([
        {"from": D1, "version_id": 1,
         "constituents": [_c("A", 0.6, A), _c("B", 0.4, B)]},
        {"from": D3, "version_id": 2,
         "constituents": [_c("A", 0.5, A), _c("B", 0.5, B)]},
    ])}
    for r in rows.values():
        assert sum(h["market_value"] for h in r["holdings"]) == pytest.approx(r["level"])
        assert sum(h["weight"] for h in r["holdings"]) == pytest.approx(1.0)
    # D2: the struck 60/40 has drifted - A is worth 72 of 112.
    by_label = {h["constituent"]["label"]: h for h in rows[D2]["holdings"]}
    assert by_label["A"]["units"] == pytest.approx(6)
    assert by_label["A"]["weight"] == pytest.approx(72 / 112)
    assert rows[D2]["period"]["version_id"] == 1
    # D3, the handover: the row belongs to the NEW basket (end-of-day),
    # struck at the old basket's level of 110 - 50/50 exactly.
    by_label = {h["constituent"]["label"]: h for h in rows[D3]["holdings"]}
    assert rows[D3]["period"]["version_id"] == 2
    assert by_label["A"]["units"] == pytest.approx(0.5 * 110 / 11)
    assert by_label["A"]["weight"] == pytest.approx(0.5)
    assert by_label["B"]["weight"] == pytest.approx(0.5)


def test_rebalance_dates_are_calendar_boundaries_after_the_start():
    start, end = dt.date(2026, 1, 15), dt.date(2026, 7, 10)
    assert rebalance_dates(start, end, "monthly") == [
        dt.date(2026, m, 1) for m in range(2, 8)]
    assert rebalance_dates(start, end, "quarterly") == [
        dt.date(2026, 4, 1), dt.date(2026, 7, 1)]
    assert rebalance_dates(start, end, "annual") == []
    assert rebalance_dates(dt.date(2025, 11, 20), end, "annual") == [dt.date(2026, 1, 1)]
    # A start ON a boundary strikes there already; the next one follows.
    assert rebalance_dates(dt.date(2026, 4, 1), end, "quarterly") == [dt.date(2026, 7, 1)]
    with pytest.raises(ValueError, match="frequency"):
        rebalance_dates(start, end, "weekly")


def test_fixed_weights_re_strike_restores_the_allocation():
    jan5, jan30, feb2, feb27 = (dt.date(2026, 1, 5), dt.date(2026, 1, 30),
                                dt.date(2026, 2, 2), dt.date(2026, 2, 27))
    A = {jan5: 10.0, jan30: 20.0, feb2: 20.0, feb27: 20.0}     # doubles, then flat
    B = {jan5: 10.0, jan30: 10.0, feb2: 10.0, feb27: 20.0}     # flat, then doubles
    period = {"from": jan5, "version_id": 7, "constituents": [_c("A", 0.5, A), _c("B", 0.5, B)]}

    drift = dict(chain_levels([period]))
    fixed_periods = expand_fixed_weights([{**period, "method": "fixed_weights",
                                           "rebalance_frequency": "monthly"}])
    assert [p["from"] for p in fixed_periods] == [jan5, dt.date(2026, 2, 1)]
    assert all(p["version_id"] == 7 for p in fixed_periods)
    fixed = dict(chain_levels(fixed_periods))

    # January is identical: one strike, drift. 150 at month end.
    assert drift[jan30] == fixed[jan30] == pytest.approx(150.0)
    # February: under drift A still weighs 2/3, so B doubling adds 50
    # (-> 200); re-struck 50/50 on Feb 1 at 150, B doubling adds 75.
    assert drift[feb27] == pytest.approx(200.0)
    assert fixed[feb27] == pytest.approx(225.0)
    # Buy-and-hold periods pass through untouched.
    assert expand_fixed_weights([period]) == [period]


def test_daily_fixed_weights_re_strike_on_every_quote_date():
    A = {D1: 10.0, D2: 20.0, D3: 20.0}     # doubles, then flat
    B = {D1: 10.0, D2: 10.0, D3: 20.0}     # flat, then doubles
    periods = expand_fixed_weights([{
        "from": D1, "version_id": 7, "method": "fixed_weights",
        "rebalance_frequency": "daily",
        "constituents": [_c("A", 0.5, A), _c("B", 0.5, B)]}])
    assert [p["from"] for p in periods] == [D1, D2]     # the last date strikes nothing
    rows = {r["date"]: r for r in chain(periods)}
    # 100 -> 150, re-struck 50/50 there, so B doubling adds 75 (drift: 50).
    assert rows[D2]["level"] == pytest.approx(150.0)
    assert rows[D3]["level"] == pytest.approx(225.0)
    assert [h["weight"] for h in rows[D2]["holdings"]] == pytest.approx([0.5, 0.5])


# ---- Total return on raw prices: units follow the adjustment factors ----
# A factor multiplies prices BEFORE its ex-date: 0.5 = 2:1 split,
# 0.98 = 2% cash dividend. chain() divides the units by it instead.

def _cf(label, weight, prices, factors):
    return {"label": label, "weight": weight, "prices": prices, "factors": factors}


def test_a_split_is_not_a_loss():
    # Raw price halves on D3 with nothing else happening: the index is flat.
    rows = {r["date"]: r for r in chain([{
        "from": D1,
        "constituents": [_cf("A", 1.0, {D1: 100.0, D2: 100.0, D3: 50.0, D4: 55.0}, {D3: 0.5})],
    }])}
    assert [rows[d]["level"] for d in (D1, D2, D3, D4)] == pytest.approx([100, 100, 100, 110])
    units = [rows[d]["holdings"][0]["units"] for d in (D1, D2, D3, D4)]
    assert units == pytest.approx([1.0, 1.0, 2.0, 2.0])
    # The stored price stays the market price, never a back-adjusted one.
    assert rows[D2]["holdings"][0]["price"] == 100.0
    assert rows[D3]["holdings"][0]["price"] == 50.0


def test_a_dividend_is_reinvested_in_the_paying_constituent():
    # A pays 2% on D3 (100 -> 98 raw); B is flat. 50/50 at base 100.
    rows = {r["date"]: r for r in chain([{
        "from": D1,
        "constituents": [
            _cf("A", 0.5, {D1: 100.0, D2: 100.0, D3: 98.0, D4: 98.0}, {D3: 0.98}),
            _c("B", 0.5, {D1: 10.0, D2: 10.0, D3: 10.0, D4: 10.0}),
        ],
    }])}
    assert [rows[d]["level"] for d in (D1, D2, D3, D4)] == pytest.approx([100, 100, 100, 100])
    a = {h["constituent"]["label"]: h for h in rows[D3]["holdings"]}["A"]
    assert a["units"] == pytest.approx(0.5 / 0.98)
    assert a["market_value"] == pytest.approx(50.0)


def test_matches_chaining_a_back_adjusted_series():
    # The same index from (raw prices + factors) and from the series
    # Bloomberg would hand back fully adjusted.
    raw = {D1: 100.0, D2: 104.0, D3: 51.0, D4: 49.5}
    factors = {D3: 0.5, D4: 0.97}                       # split, then a 3% dividend
    adjusted = {D1: 100.0 * 0.5 * 0.97, D2: 104.0 * 0.5 * 0.97, D3: 51.0 * 0.97, D4: 49.5}
    other = {D1: 20.0, D2: 21.0, D3: 19.0, D4: 22.0}

    from_raw = chain_levels([{"from": D1, "constituents": [
        _cf("A", 0.7, raw, factors), _c("B", 0.3, other)]}])
    from_adjusted = chain_levels([{"from": D1, "constituents": [
        _c("A", 0.7, adjusted), _c("B", 0.3, other)]}])
    assert [lv for _, lv in from_raw] == pytest.approx([lv for _, lv in from_adjusted])


def test_ex_date_without_a_quote_waits_for_the_first_ex_price():
    # A goes ex on D3 but only quotes on D4; B quotes daily, so D3 is in
    # the grid with A's forward-filled CUM price. Growing the units there
    # would show a 2% gain that reverses on D4.
    rows = {r["date"]: r for r in chain([{
        "from": D1,
        "constituents": [
            _cf("A", 0.5, {D1: 100.0, D2: 100.0, D4: 98.0}, {D3: 0.98}),
            _c("B", 0.5, {D1: 10.0, D2: 10.0, D3: 10.0, D4: 10.0}),
        ],
    }])}
    assert [rows[d]["level"] for d in (D1, D2, D3, D4)] == pytest.approx([100, 100, 100, 100])


def test_a_factor_already_in_the_strike_price_is_not_applied_again():
    # Struck ON the ex-date at the ex price: nothing to reinvest. A later
    # factor still applies; a re-strike after it starts clean again.
    A = {D1: 100.0, D2: 50.0, D3: 50.0, D4: 49.0}
    factors = {D2: 0.5, D4: 0.98}
    levels = dict(chain_levels([
        {"from": D2, "constituents": [_cf("A", 1.0, A, factors)]},
    ]))
    assert [levels[d] for d in (D2, D3, D4)] == pytest.approx([100, 100, 100])

    # Handover on the ex-date D4: the old basket earns the dividend on D4,
    # the new one is struck at the ex price and does not earn it twice.
    rows = {r["date"]: r for r in chain([
        {"from": D2, "version_id": 1, "constituents": [_cf("A", 1.0, A, factors)]},
        {"from": D4, "version_id": 2, "constituents": [_cf("A", 1.0, A, factors)]},
    ])}
    assert rows[D4]["level"] == pytest.approx(100.0)
    assert rows[D4]["period"]["version_id"] == 2
    assert rows[D4]["holdings"][0]["units"] == pytest.approx(100.0 / 49.0)


def test_future_factors_do_not_change_earlier_rows():
    # A row must be reproducible bit for bit before and after a later
    # dividend is known: stored rows are never rewritten by new factors.
    A = {D1: 100.0, D2: 103.0, D3: 51.0, D4: 50.0}
    before = chain([{"from": D1, "constituents": [_cf("A", 1.0, A, {D3: 0.5})]}])
    after = chain([{"from": D1, "constituents": [_cf("A", 1.0, A, {D3: 0.5, D4: 0.98})]}])
    for b, a in zip(before[:3], after[:3]):
        assert (b["level"], b["holdings"][0]["units"]) == (a["level"], a["holdings"][0]["units"])
