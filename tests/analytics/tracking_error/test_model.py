# tests/analytics/tracking_error/test_model.py
# ---------------------------------------------------------------
# Tests for the pure half of the tracking error. No DB.
#
# Pinned down:
#   - with complete data a' S a IS the variance of the simulated
#     active return series (the identity the whole measure rests on)
#   - a 2-asset case computed by hand; zero active weights give 0
#   - contributions sum to TE
#   - backfill: real days untouched, filled days = beta * bucket,
#     beta recovered, diagonal term = s2 * n_filled / n_total
#   - proxy tier: beta 1 and the bucket's median residual variance
#   - prices forward-fill a bounded number of grid days
#   - run.compute end to end: FX legs on securities, cash and
#     forwards reproduce the hand-built PEN active series, and the
#     reporting currency drops out when active weights sum to zero
# ---------------------------------------------------------------

import numpy as np
import pandas as pd
import pytest

from src.pipelines.analytics.tracking_error.model import (
    TRADING_DAYS, backfill_short, covariance, daily_returns, tracking_error,
)


def _returns(n_days=756, n_assets=5, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(rng.normal(0, 0.01, (n_days, n_assets)),
                        columns=[f"A{i}" for i in range(n_assets)])


def _no_fill(r):
    return r.isna(), pd.Series(0.0, index=r.columns)


def test_quadratic_form_is_variance_of_active_series():
    r = _returns()
    a = pd.Series([0.3, -0.1, 0.05, -0.2, -0.05], index=r.columns)
    te, contrib = tracking_error(a, covariance(r, *_no_fill(r)))
    assert te ** 2 == pytest.approx((r @ a).var() * TRADING_DAYS, rel=1e-12)
    assert contrib.sum() == pytest.approx(te, rel=1e-12)


def test_two_assets_by_hand():
    cov = pd.DataFrame([[0.04, 0.01], [0.01, 0.09]], index=["x", "y"], columns=["x", "y"])
    te, _ = tracking_error(pd.Series({"x": 0.5, "y": -0.5}), cov)
    # 0.25*0.04 + 0.25*0.09 - 2*0.25*0.01 = 0.0275
    assert te == pytest.approx(np.sqrt(0.0275))


def test_zero_active_weights():
    r = _returns()
    te, contrib = tracking_error(pd.Series(0.0, index=r.columns), covariance(r, *_no_fill(r)))
    assert te == 0.0 and (contrib == 0).all()


def test_beta_scaled_backfill():
    rng = np.random.default_rng(1)
    n, n_real, beta, sd = 756, 200, 1.5, 0.004
    peers = pd.DataFrame(rng.normal(0, 0.01, (n, 4)), columns=list("abcd"))
    bucket_r = peers.mean(axis=1)
    new = beta * bucket_r + rng.normal(0, sd, n)
    new.iloc[: n - n_real] = np.nan
    r = peers.assign(new=new)

    filled, info = backfill_short(r, {c: "eq_USD" for c in r.columns})

    assert info.loc["new", "tier"] == "short"
    assert info.loc["new", "n_filled"] == n - n_real
    assert info.loc["new", "beta"] == pytest.approx(beta, abs=0.2)
    assert info.loc["new", "s2"] == pytest.approx(sd ** 2, rel=0.3)
    b = info.loc["new", "beta"]
    np.testing.assert_array_equal(filled["new"].iloc[n - n_real:], new.iloc[n - n_real:])
    np.testing.assert_allclose(filled["new"].iloc[: n - n_real], b * bucket_r.iloc[: n - n_real])
    pd.testing.assert_frame_equal(filled[list("abcd")], peers)
    assert not filled.isna().any().any()

    cov = covariance(filled, r.isna(), info["s2"])
    plain = filled["new"].var() * TRADING_DAYS
    added = info.loc["new", "s2"] * (n - n_real) / n * TRADING_DAYS
    assert cov.loc["new", "new"] == pytest.approx(plain + added, rel=1e-12)
    assert cov.loc["a", "a"] == pytest.approx(peers["a"].var() * TRADING_DAYS, rel=1e-12)
    assert np.linalg.eigvalsh(cov.to_numpy()).min() > -1e-12


def test_proxy_and_uncovered_tiers():
    rng = np.random.default_rng(2)
    n = 756
    peers = pd.DataFrame(rng.normal(0, 0.01, (n, 3)), columns=list("abc"))
    short = 0.8 * peers.mean(axis=1) + rng.normal(0, 0.003, n)
    short.iloc[:600] = np.nan                       # 156 obs -> regression
    tiny = pd.Series(rng.normal(0, 0.01, n))
    tiny.iloc[:746] = np.nan                        # 10 obs -> proxy
    r = peers.assign(short=short, tiny=tiny, orphan=tiny)
    buckets = {c: "eq_USD" for c in ["a", "b", "c", "short", "tiny"]}   # orphan: none

    filled, info = backfill_short(r, buckets)

    assert info.loc["tiny", "tier"] == "proxy" and info.loc["tiny", "beta"] == 1.0
    regressed = info.loc[["a", "b", "c", "short"], "s2"]
    assert info.loc["tiny", "s2"] == pytest.approx(regressed.median())
    np.testing.assert_allclose(filled["tiny"].iloc[:746], peers.mean(axis=1).iloc[:746])
    assert info.loc["orphan", "tier"] == "uncovered" and info.loc["orphan", "s2"] == 0.0
    assert (filled["orphan"].iloc[:746] == 0).all()


def test_daily_returns_grid_and_bounded_ffill():
    days = pd.bdate_range("2026-01-05", periods=10)
    px = pd.DataFrame({"x": 100 * 1.01 ** np.arange(10.0), "gap": 50.0}, index=days)
    px.loc[days[2], "x"] = np.nan                   # one missing print: carried
    px.loc[days[4:9], "gap"] = np.nan               # five missing: beyond the limit
    r = daily_returns(px, days, ffill_limit=3)
    assert len(r) == 9
    assert r["x"].iloc[1] == 0.0                    # carried price -> zero return
    assert r["x"].iloc[2] == pytest.approx(2 * np.log(1.01))   # catch-up
    assert r["gap"].iloc[3:6].eq(0).all() and r["gap"].iloc[6:].isna().all()


def test_compute_end_to_end_currency_legs():
    # Fund: a PEN stock, a USD stock, USD cash, and a forward selling
    # USD. Benchmark: one PEN index. Checked against the simulated
    # active return series built by hand in PEN.
    from src.pipelines.analytics.tracking_error.run import compute

    rng = np.random.default_rng(3)
    days = pd.bdate_range("2023-01-02", periods=400)
    lvl = lambda sd: pd.Series(100 * np.exp(np.cumsum(rng.normal(0, sd, 400))), index=days)
    px = pd.DataFrame({"1": lvl(0.01), "2": lvl(0.012), "9": lvl(0.006)})
    fx = pd.DataFrame({"CCY_USD": lvl(0.004)})
    w_port = {"1": 0.5, "2": 0.3, "CCY_USD": 0.2 - 0.4, "CCY_PEN": 0.4}
    w_bench = {"9": 1.0}

    res = compute(w_port, w_bench, px, fx, {"1": None, "2": "CCY_USD", "9": None},
                  {"1": "146_PEN", "2": "146_USD"}, list(days))

    r = np.log(px).diff().iloc[1:]
    f = np.log(fx["CCY_USD"]).diff().iloc[1:]
    active = 0.5 * r["1"] + 0.3 * (r["2"] + f) - 0.2 * f - r["9"]
    assert res["te"] == pytest.approx(np.sqrt(active.var() * TRADING_DAYS), rel=1e-10)
    assert res["te_zero_vol"] == pytest.approx(res["te"])       # all on full history
    assert res["detail"]["contribution"].sum() == pytest.approx(res["te"])

    usd = compute(w_port, w_bench, px, fx, {"1": None, "2": "CCY_USD", "9": None},
                  {"1": "146_PEN", "2": "146_USD"}, list(days), currency="USD")
    # Active weights sum to zero here, so the reporting currency drops out.
    assert usd["te"] == pytest.approx(res["te"], rel=1e-10)
