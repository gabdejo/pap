# src/pipelines/analytics/tracking_error/model.py
# ---------------------------------------------------------------
# Pure half of the ex-ante tracking error: pandas/numpy in, pandas
# out, no database. TE = sqrt(a' S a), a = active weights, S = the
# annualised covariance of daily log returns in one currency.
#
#   daily_returns   prices -> log returns on a fixed date grid
#   backfill_short  completes the matrix: a missing day of an asset is
#                   beta * (its bucket's return); the idiosyncratic
#                   variance the fill lacks comes back on the diagonal
#   covariance      equal-weighted or EWMA, plus that diagonal term
#   tracking_error  TE and the per-asset contributions (sum to TE)
#
# Tiers (backfill_short), by real observations in the window:
#   full       >= MIN_FULL; member of its bucket's return. Any missing
#              day is still filled, with its own regression.
#   short      MIN_REGRESSION..MIN_FULL-1; regression on the bucket.
#   proxy      fewer; beta = 1, residual variance = the bucket median.
#   uncovered  too few points and no bucket to lean on: zero return.
# An asset with no bucket (a currency, a benchmark-only leg) and
# enough points keeps beta = 0 and its own variance on the diagonal.
#
# The residual is assumed uncorrelated with everything else, so only
# the diagonal moves and S stays positive semi-definite.
# ---------------------------------------------------------------

import numpy as np
import pandas as pd

TRADING_DAYS = 252
MIN_FULL = 252
MIN_REGRESSION = 60
STALE_ZERO_SHARE = 0.5


def daily_returns(prices: pd.DataFrame, grid, ffill_limit: int = 3) -> pd.DataFrame:
    """
    Log returns of each price column over consecutive grid dates. A
    price missing on a grid date is carried forward at most
    ffill_limit rows; beyond that the return is NaN (backfill_short
    deals with it). len(grid) - 1 rows.
    """
    grid = pd.DatetimeIndex(grid)
    prices = prices.set_axis(pd.DatetimeIndex(prices.index)).sort_index()
    on_grid = (prices.reindex(prices.index.union(grid))
               .ffill(limit=ffill_limit).reindex(grid))
    return np.log(on_grid.where(on_grid > 0)).diff().iloc[1:]


def _ols(r: pd.Series, b: pd.Series) -> tuple[float, float]:
    """(beta, residual variance) of r on b, with intercept."""
    var_b = b.var()
    beta = float(r.cov(b) / var_b) if var_b > 0 else 0.0
    return beta, float((r - beta * b).var())


def backfill_short(
    returns: pd.DataFrame,
    buckets: dict,
    min_full: int = MIN_FULL,
    min_regression: int = MIN_REGRESSION,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Fills every NaN of `returns`. `buckets` maps a column to its bucket
    label (missing or None = no bucket). Returns (filled, info); info
    is indexed by column: tier, n_obs, n_filled, beta, s2 (daily
    residual variance, what covariance() puts back on the diagonal).
    """
    n_obs = returns.notna().sum()
    bucket = {c: buckets.get(c) for c in returns.columns}
    members: dict = {}
    for c in returns.columns:
        if bucket[c] is not None and n_obs[c] >= min_full:
            members.setdefault(bucket[c], []).append(c)

    filled = returns.copy()
    rows = {}
    for c in returns.columns:
        r = returns[c]
        n = int(n_obs[c])
        # Leave-one-out: an asset is never regressed on itself.
        peers = [m for m in members.get(bucket[c], []) if m != c]
        b = returns[peers].mean(axis=1) if peers else None
        beta, s2 = 0.0, 0.0
        if n >= min_regression:
            tier = "full" if n >= min_full else "short"
            both = r.notna() & b.notna() if b is not None else None
            if both is not None and both.sum() >= min_regression:
                # ponytail: one bucket factor, beta not shrunk toward 1.
                # Shrink, or go multi-factor, if short names show wild betas.
                beta, s2 = _ols(r[both], b[both])
            else:
                s2 = float(r.var())
        elif b is not None:
            tier, beta, s2 = "proxy", 1.0, np.nan      # s2 set below
        else:
            tier = "uncovered"
        fill = beta * b.fillna(0.0) if b is not None else 0.0
        filled[c] = r.fillna(fill)
        rows[c] = {"tier": tier, "n_obs": n, "n_filled": len(r) - n,
                   "beta": beta, "s2": s2, "bucket": bucket[c]}

    info = pd.DataFrame.from_dict(rows, orient="index")
    regressed = info[info["tier"].isin(["full", "short"]) & info["bucket"].notna()]
    median_s2 = regressed.groupby("bucket")["s2"].median()
    proxy = info["tier"] == "proxy"
    info.loc[proxy, "s2"] = info.loc[proxy, "bucket"].map(median_s2).fillna(0.0)
    return filled, info.drop(columns="bucket")


def covariance(
    filled: pd.DataFrame,
    missing: pd.DataFrame,
    s2: pd.Series,
    halflife_days: float | None = None,
) -> pd.DataFrame:
    """
    Annualised covariance of a complete return matrix. `missing` is the
    boolean frame of the days backfill_short filled; each asset gets
    s2 * (weight of its filled days) added on the diagonal. Equal
    weights (sample covariance) unless halflife_days is given (EWMA).
    """
    x = filled.to_numpy(dtype=float)
    t = len(x)
    if halflife_days is None:
        w = np.full(t, 1.0 / t)
        scale = t / (t - 1)
    else:
        w = 0.5 ** (np.arange(t - 1, -1, -1) / halflife_days)
        w /= w.sum()
        scale = 1.0
    xc = x - w @ x
    cov = (xc * w[:, None]).T @ xc * scale
    cov[np.diag_indices_from(cov)] += (
        s2.reindex(filled.columns).fillna(0.0).to_numpy()
        * (w @ missing[filled.columns].to_numpy(dtype=float)))
    return pd.DataFrame(cov * TRADING_DAYS, index=filled.columns, columns=filled.columns)


def tracking_error(active: pd.Series, cov: pd.DataFrame) -> tuple[float, pd.Series]:
    """
    (TE, contributions). An asset of `active` absent from `cov` has no
    risk; contributions are a_i * (S a)_i / TE and sum to TE.
    """
    a = active.reindex(cov.index).fillna(0.0).to_numpy(dtype=float)
    sa = cov.to_numpy() @ a
    te = float(np.sqrt(max(a @ sa, 0.0)))
    contrib = a * sa / te if te > 0 else np.zeros_like(a)
    return te, pd.Series(contrib, index=cov.index)


def stale(returns: pd.DataFrame, zero_share: float = STALE_ZERO_SHARE) -> list:
    """Columns whose real returns are mostly exact zeros (repeated prices)."""
    n = returns.notna().sum()
    zeros = (returns == 0).sum()
    return [c for c in returns.columns if n[c] > 0 and zeros[c] / n[c] > zero_share]
