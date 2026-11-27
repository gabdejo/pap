# src/pipelines/analytics/tracking_error/run.py
# ---------------------------------------------------------------
# Entry point of the ex-ante tracking error of a fund against a
# benchmark on one date. Computed on demand, nothing is stored.
#
#   run()      opens the connection, reads (data.py), calls compute()
#   compute()  pure: weights + prices -> TE. Tests drive it directly.
#
# Method (docs/TRACKING_ERROR_INPUTS.md has the input runbook):
#   - daily log returns on the SBS reporting-day grid, window_days
#     observations, annualised x252
#   - every asset's return is in PEN: local return + the log return of
#     its FX leg. A PEN exposure ('CCY_PEN') has zero return.
#   - short histories are completed by the beta-scaled backfill
#     (model.backfill_short), then one covariance over the union of
#     portfolio and benchmark assets
#   - te_zero_vol repeats the calculation with every non-'full' asset
#     removed from the active weights: the gap to te is how much of
#     the answer rests on the backfill
#   - currency != PEN subtracts that currency's return from every
#     column. Active weights sum to about zero, so TE barely moves.
#
# FX_SHIFT_DAYS: rows to shift the SBS currency returns. The SBS
# tipo_cambio extract stamps the run date, not the file's FECHA; set
# -1 if the office check (runbook A3) shows the rate is a day late.
# ---------------------------------------------------------------

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.calendars import reporting_days_in_range
from src.db.connection import get_connection
from src.pipelines.analytics.tracking_error import data, model

logger = logging.getLogger(__name__)

FX_SHIFT_DAYS = 0
WINDOW_DAYS = 756


def _returns(prices: pd.DataFrame, grid) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(index=pd.DatetimeIndex(grid)[1:])
    return model.daily_returns(prices, grid)


def compute(
    w_port: dict,
    w_bench: dict,
    asset_prices: pd.DataFrame,
    fx_prices: pd.DataFrame,
    asset_fx: dict,
    buckets: dict,
    grid,
    halflife_days: float | None = None,
    currency: str = data.BASE,
) -> dict:
    """
    Pure calculation. Weights are {asset key: weight}; asset_prices is
    date x asset key (local currency), fx_prices date x FX key (PEN per
    unit), asset_fx {asset key: FX key or None}. See data.py for keys.
    """
    base_key, report_key = data.ccy_key(data.BASE), data.ccy_key(currency)
    keys = set(w_port) | set(w_bench)
    if currency != data.BASE:
        keys.add(report_key)            # needed as a column to convert with
    keys = sorted(keys)
    local, fx = _returns(asset_prices, grid), _returns(fx_prices, grid)
    sbs = [c for c in fx.columns if c.startswith("CCY_")]
    fx[sbs] = fx[sbs].shift(FX_SHIFT_DAYS)

    nan = pd.Series(np.nan, index=local.index)
    columns, fx_missing = {}, set()
    for key in keys:
        if key == base_key:
            columns[key] = nan.fillna(0.0)
        elif key.startswith("CCY_"):
            columns[key] = fx[key] if key in fx else nan
            if key not in fx:
                fx_missing.add(key)
        elif key in local:
            leg = asset_fx.get(key)
            if leg and leg not in fx:
                fx_missing.add(leg)
            columns[key] = local[key] + fx[leg] if leg in fx else local[key]
        else:
            columns[key] = nan
    returns = pd.DataFrame(columns, index=local.index)

    filled, info = model.backfill_short(returns, buckets)
    if currency != data.BASE:
        if report_key in fx_missing:
            raise ValueError(f"No FX series for {currency}; cannot report in it.")
        filled = filled.sub(filled[report_key], axis=0)
    cov = model.covariance(filled, returns.isna(), info["s2"], halflife_days)

    port = pd.Series(w_port, dtype=float).reindex(keys).fillna(0.0)
    bench = pd.Series(w_bench, dtype=float).reindex(keys).fillna(0.0)
    active = port - bench
    te, contribution = model.tracking_error(active, cov)
    te_zero_vol, _ = model.tracking_error(active.where(info["tier"] == "full", 0.0), cov)

    detail = info.assign(w_port=port, w_bench=bench, active=active,
                         contribution=contribution)
    return {
        "te": te,
        "te_zero_vol": te_zero_vol,
        "n_days": len(returns),
        "detail": detail,
        "stale": [k for k in model.stale(returns) if k != base_key],
        "fx_missing": sorted(fx_missing),
    }


def _grid(as_of: date, window_days: int) -> list[date]:
    start = as_of - timedelta(days=int(window_days * 1.6) + 30)
    return reporting_days_in_range(start, as_of)[-(window_days + 1):]


def run(
    portfolio: str,
    benchmark: str,
    as_of: date,
    window_days: int = WINDOW_DAYS,
    halflife_days: float | None = None,
    currency: str = data.BASE,
) -> dict:
    """
    Ex-ante tracking error of fund `portfolio` against `benchmark`
    (both dim_portfolio procodes) with the weights of `as_of`. Returns
    compute()'s dict plus labels, the weight sums and the FX sources.
    """
    grid = _grid(as_of, window_days)
    start = grid[0] - timedelta(days=10)

    with get_connection() as conn:
        port = data.portfolio_row(conn, portfolio, benchmark=False)
        bench = data.portfolio_row(conn, benchmark, benchmark=True)
        w_port, meta = data.portfolio_weights(conn, port, as_of)
        w_bench, legs = data.benchmark_weights(conn, bench["portfolio_id"], as_of)
        # A held entity keeps the series the holdings rule picked.
        series = {**legs, **data.select_series(conn, meta)}
        fx_ids = sorted({int(s["fx"][1:]) for s in series.values()
                         if s["fx"] and s["fx"].startswith("S")})
        px = data.prices(
            conn,
            [s["series_id"] for s in series.values() if s["adjusted"]],
            [s["series_id"] for s in series.values() if not s["adjusted"]] + fx_ids,
            start, as_of)
        fx_prices, fx_sources = data.fx_mid(conn, start, as_of)
        labels = data.names(
            conn, [int(k) for k in set(w_port) | set(w_bench) if k.isdigit()])

    asset_prices = pd.DataFrame(
        {k: px[s["series_id"]] for k, s in series.items() if s["series_id"] in px})
    for sid in fx_ids:
        if sid in px:
            fx_prices[f"S{sid}"] = px[sid]

    result = compute(
        w_port, w_bench, asset_prices, fx_prices,
        asset_fx={k: s["fx"] for k, s in series.items()},
        buckets={k: f"{m['ind_clase']}_{m['currency']}" for k, m in meta.items()},
        grid=grid, halflife_days=halflife_days, currency=currency.upper())

    detail = result["detail"]
    detail.insert(0, "name", [labels.get(k, k) for k in detail.index])
    result.update(
        portfolio=portfolio, benchmark=benchmark, as_of=as_of, currency=currency.upper(),
        fx_sources=fx_sources,
        # Forward legs net to zero, so the portfolio sum is NAV coverage.
        w_port_sum=float(detail["w_port"].sum()),
        w_bench_sum=float(detail["w_bench"].sum()))
    logger.info(
        f"{portfolio} vs {benchmark} {as_of}: TE {result['te']:.4%} "
        f"(zero-vol short names {result['te_zero_vol']:.4%}), "
        f"{len(detail)} assets, {result['n_days']} days")
    return result
