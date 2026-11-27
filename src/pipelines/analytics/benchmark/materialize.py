# src/pipelines/analytics/benchmark/materialize.py
# ---------------------------------------------------------------
# Turns a benchmark's definition versions + fact_prices into facts.
# One run, three outputs (docs/PENDING_PIPELINES.md item 5):
#   1. dated holdings into fact_positions_model, tagged with the
#      version_id that produced them;
#   2. a fact_portfolio_valuation row per date (the weight denominator
#      vw_positions_unified divides by);
#   3. the level series into fact_prices on the index entity,
#      source 'model'.
# Everything in one transaction: a failed run leaves the stored
# series untouched.
#
# Semantics:
#   - Levels are base 100 at the first version's effective_from and
#     chained across versions: a new version re-strikes its holdings
#     at the level the old one reached, so the index is continuous.
#   - buy_and_hold: weights are the allocation AT effective_from only;
#     afterwards each holding floats with its price (weights drift).
#   - fixed_weights: the same allocation is re-struck at every
#     rebalance boundary (monthly | quarterly | annual: calendar period
#     starts; daily: every date a constituent quotes) until the next
#     version takes over.
#   - heuristic: a named rule without a module yet; refused.
#   - The notional is explicit and unitless: valor_cartera = level,
#     valor_cuota = level (one quota outstanding), so
#     SUM(market_value_pen) per date = level and weights sum to 1.
#   - TOTAL RETURN on raw prices: fact_prices is unadjusted, so each
#     constituent carries its price adjustment factors
#     (fact_adjustment_factors, docs/BLOOMBERG_SBS.md section 6) and its
#     units are divided by the factor when an ex-date is crossed. A 2:1
#     split (0.5) doubles the units as the price halves; a 2% dividend
#     (0.98) grows them ~2%, i.e. reinvested in the paying constituent.
#     Stored units are share counts and stored prices are market prices,
#     and no row depends on a dividend that has not happened yet. A
#     constituent without factors (indices, SPP NAVs) behaves as before.
#   - Holdings on a date are END-OF-DAY: on a handover date they are
#     the new basket's, struck at that day's level, which is what the
#     next day's attribution needs. The level on the handover date is
#     the old basket's mark-to-market (the two agree by construction).
#
# The chaining arithmetic is PURE (chain(), chain_levels()) over plain
# dicts, so it is tested without a database. The database half reads
# the governing versions (definitions.governing_versions), their
# constituents' prices, folds in the FX leg and writes the three
# outputs row by row.
#
# Rows are never deleted. fact_positions_model keys on version_id, so
# a restatement writes new rows and the old version's stay (the
# current view picks the highest version_id per date). The level and
# the valuation rows have no version in their key: they are the
# current restatement, upserted in place. If a rebuild covers FEWER
# dates than the stored series (a later first effective_from), the
# stale levels are left alone and counted in the summary.
# ---------------------------------------------------------------

import datetime as dt
import logging
from bisect import bisect_left, bisect_right

from src.db.queries import update_series_run_metadata
from src.pipelines.analytics.benchmark import definitions

logger = logging.getLogger(__name__)

BASE_LEVEL = 100.0
SOURCE_MODEL = definitions.SOURCE_MODEL

# Same replace semantics as the SBS feeds (see the valor cuota loader
# and upstream fbb27ab): a changed level is rewritten, an identical one
# is left alone and reported as unchanged.
UPSERT_LEVEL = """
    INSERT INTO fact_prices (series_id, date, price, source)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (series_id, date) DO UPDATE SET
        price = EXCLUDED.price,
        source = EXCLUDED.source
    WHERE fact_prices.price IS DISTINCT FROM EXCLUDED.price
"""

UPSERT_VALUATION = """
    INSERT INTO fact_portfolio_valuation
        (portfolio_id, date, source, valor_cuota, valor_cartera)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (portfolio_id, date, source) DO UPDATE SET
        valor_cuota = EXCLUDED.valor_cuota,
        valor_cartera = EXCLUDED.valor_cartera,
        loaded_at = CURRENT_TIMESTAMP
    WHERE fact_portfolio_valuation.valor_cuota IS DISTINCT FROM EXCLUDED.valor_cuota
       OR fact_portfolio_valuation.valor_cartera IS DISTINCT FROM EXCLUDED.valor_cartera
"""

# Within one version a re-run refreshes its own rows; other versions'
# rows are never touched (version_id is in the key).
UPSERT_HOLDING = """
    INSERT INTO fact_positions_model
        (portfolio_id, date, entity_id, version_id, source,
         units, price, market_value_pen, weight)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (portfolio_id, date, entity_id, version_id, source) DO UPDATE SET
        units = EXCLUDED.units,
        price = EXCLUDED.price,
        market_value_pen = EXCLUDED.market_value_pen,
        weight = EXCLUDED.weight,
        loaded_at = CURRENT_TIMESTAMP
    WHERE fact_positions_model.units IS DISTINCT FROM EXCLUDED.units
       OR fact_positions_model.price IS DISTINCT FROM EXCLUDED.price
       OR fact_positions_model.market_value_pen IS DISTINCT FROM EXCLUDED.market_value_pen
       OR fact_positions_model.weight IS DISTINCT FROM EXCLUDED.weight
"""


# ---- The arithmetic: chaining with drift ------------------------------

def _quote_at(prices: dict, dates: list, day) -> tuple:
    """Forward-filled price: the last quote at or before `day`, with the
    date it is from - (quote_date, price), or (None, None) before the
    first quote. `dates` is sorted(prices), computed once per constituent
    by the caller: a binary search per lookup instead of a sort, which
    made chain() quadratic in dates (4.7 s for a 1993-onward benchmark)."""
    i = bisect_right(dates, day)
    return (dates[i - 1], prices[dates[i - 1]]) if i else (None, None)


def _window(dates: list, start, end) -> list:
    """The sorted `dates` within [start, end]; end=None is open-ended."""
    hi = len(dates) if end is None else bisect_right(dates, end)
    return dates[bisect_left(dates, start):hi]


def _cum_factors(factors: dict) -> tuple[list, list]:
    """(sorted ex-dates, running product of their factors)."""
    dates = sorted(factors)
    running, acc = [], 1.0
    for d in dates:
        acc *= factors[d]
        running.append(acc)
    return dates, running


def _factor_between(cum: tuple[list, list], after, upto) -> float:
    """Product of the factors with ex-date in (after, upto]."""
    dates, running = cum
    lo, hi = bisect_right(dates, after), bisect_right(dates, upto)
    if hi <= lo:
        return 1.0
    return running[hi - 1] / (running[lo - 1] if lo else 1.0)


def chain(periods: list[dict], base: float = BASE_LEVEL) -> list[dict]:
    """
    PURE chaining of a drifting (buy-and-hold) composite index, with
    the holdings behind every level.

    periods: [{'from': date, 'constituents': [
                  {'label': str, 'weight': float, 'prices': {date: px},
                   'factors': {ex_date: f}   (optional),
                   ...any other keys, carried through to the holdings}
              ], ...any other keys (e.g. 'version_id'), carried through}]
    ordered by 'from'. Each period runs until the next one starts (the
    last runs to its constituents' final price).

    At each period start the index capital splits by weight into
    holdings (units = weight * level / price); between rebalances the
    index is the mark-to-market of those fixed units, so weights
    drift. Prices forward-fill inside the grid (union of constituent
    dates); a constituent with no price at or before a period start
    fails loud naming itself - a silently dropped leg would misprice
    the index.

    'prices' are RAW. 'factors' are the constituent's price adjustment
    factors (multiply prices before ex_date by f). Units are divided by
    every factor whose ex-date lies after the quote the units were
    struck at and at or before the quote being marked - so the units
    change on the first date the constituent actually QUOTES ex, not on
    a forward-filled cum price, and a factor already inside the strike
    price is not applied twice. Without 'factors' the units are fixed.

    Returns one row per date, across all periods, base at the first
    period's start:
        {'date', 'level', 'period' (the period dict),
         'holdings': [{'constituent', 'units', 'price',
                       'market_value', 'weight'}]}
    A handover date closes one period and opens the next at the same
    level; its row carries the NEW period's holdings (end-of-day).
    """
    rows: dict = {}
    level = base
    # sorted(prices) once per price dict, shared across the synthetic
    # re-strike periods that reuse the same constituent dicts.
    sorted_dates: dict[int, list] = {}

    def dates_of(c: dict) -> list:
        key = id(c["prices"])
        if key not in sorted_dates:
            sorted_dates[key] = sorted(c["prices"])
        return sorted_dates[key]

    cum_factors: dict[int, tuple] = {}

    def cum_of(c: dict) -> tuple | None:
        factors = c.get("factors")
        if not factors:
            return None
        if id(factors) not in cum_factors:
            cum_factors[id(factors)] = _cum_factors(factors)
        return cum_factors[id(factors)]

    for k, period in enumerate(periods):
        start = period["from"]
        end = periods[k + 1]["from"] if k + 1 < len(periods) else None
        constituents = period["constituents"]
        if not constituents:
            raise ValueError(f"Empty basket from {start}.")

        # Common grid: constituent dates within [start, end] - the next
        # handover date INCLUDED. That day belongs to the OLD basket:
        # the new one takes over from the following close. Ending the
        # period the day before meant the next period struck its
        # holdings at the previous close's level and priced them at the
        # handover date, so the index came out flat that day and the
        # real return was lost - once per rebalance, always.
        # Sliced by binary search, not scanned: a daily-rebalanced basket
        # is one period per date, and a scan per period is quadratic.
        grid = sorted({d for c in constituents
                       for d in _window(dates_of(c), start, end)})
        if not grid or grid[0] != start:
            # The period must start pricing AT its effective date; the
            # holdings are struck there.
            grid = [start] + [d for d in grid if d > start]
        if end is not None and grid[-1] != end:
            # Nobody quoted exactly on the handover date: carry the last
            # known prices to it, so the baton always changes hands there.
            grid.append(end)

        # (constituent, units at the strike, date of the strike quote)
        holdings = []
        for c in constituents:
            q0, p0 = _quote_at(c["prices"], dates_of(c), start)
            if p0 is None or p0 <= 0:
                raise ValueError(
                    f"'{c['label']}' has no price at or before the "
                    f"effective date {start}.")
            holdings.append((c, (c["weight"] * level) / p0, q0))

        for i, d in enumerate(grid):
            marked = []
            value = 0.0
            for c, units0, q0 in holdings:
                q, px = _quote_at(c["prices"], dates_of(c), d)
                if px is None:
                    raise ValueError(
                        f"'{c['label']}' has no price at or before {d}.")
                cum = cum_of(c)
                units = units0 / _factor_between(cum, q0, q) if cum else units0
                marked.append((c, units, px, units * px))
                value += units * px
            # The strike date keeps the level the units were struck at
            # (the base, or the level the previous basket handed over):
            # recomputing it as units * price only adds rounding noise,
            # which on a handover date would leave two values for one
            # level and make a partial rebuild differ from a full one.
            if i > 0:
                level = value
            # A duplicated date (the handover) keeps the LAST computation:
            # the new basket's holdings, at the level the old one reached.
            rows[d] = {
                "date": d,
                "level": level,
                "period": period,
                "holdings": [
                    {"constituent": c, "units": units, "price": px,
                     "market_value": mv,
                     "weight": (mv / level) if level else 0.0}
                    for c, units, px, mv in marked
                ],
            }

    return [rows[d] for d in sorted(rows)]


def chain_levels(periods: list[dict], base: float = BASE_LEVEL) -> list[tuple]:
    """The level series alone: [(date, level)], see chain()."""
    return [(r["date"], r["level"]) for r in chain(periods, base)]


def rebalance_dates(start, end, frequency: str) -> list:
    """
    Calendar boundaries strictly after `start` and at or before `end`
    where a fixed-weights basket is re-struck: the first day of every
    month, quarter or year. A boundary that is not a trading day is
    fine: chain() forward-fills prices onto it.
    """
    months = {"monthly": 1, "quarterly": 3, "annual": 12}
    if frequency not in months:
        raise ValueError(
            f"Unknown rebalance frequency {frequency!r}; "
            f"one of {', '.join(sorted(months))}.")
    step = months[frequency]
    # First boundary: the next period start after `start`, aligned to
    # the calendar (quarters start in Jan/Apr/Jul/Oct, years in Jan).
    y, m = start.year, start.month
    m = ((m - 1) // step + 1) * step + 1
    while m > 12:
        y, m = y + 1, m - 12
    out = []
    while True:
        boundary = dt.date(y, m, 1)
        if boundary > end:
            break
        if boundary > start:
            out.append(boundary)
        m += step
        while m > 12:
            y, m = y + 1, m - 12
    return out


def expand_fixed_weights(periods: list[dict]) -> list[dict]:
    """
    Inserts synthetic re-strike periods for every fixed_weights period:
    the same basket from each rebalance boundary until the next
    version. Buy-and-hold periods pass through unchanged. Pure.
    """
    out = []
    for k, period in enumerate(periods):
        out.append(period)
        if period.get("method") != "fixed_weights":
            continue
        start = period["from"]
        if k + 1 < len(periods):
            end = periods[k + 1]["from"]
        else:
            end = max((d for c in period["constituents"] for d in c["prices"]),
                      default=start)
        if period["rebalance_frequency"] == "daily":
            # Every date a constituent quotes: a re-strike on a day with
            # no new price would restore weights that have not moved.
            boundaries = sorted({d for c in period["constituents"]
                                 for d in c["prices"] if d > start})
        else:
            boundaries = rebalance_dates(start, end, period["rebalance_frequency"])
        for boundary in boundaries:
            if boundary >= end:
                break
            out.append({**period, "from": boundary, "synthetic": True})
    return out


# ---- Database half -----------------------------------------------------

def _prices(conn, series_id: int) -> dict:
    """{date: price} of one series, whole history."""
    cur = conn.cursor()
    cur.execute(
        "SELECT date, price FROM fact_prices WHERE series_id = %s ORDER BY date",
        (series_id,))
    return {r["date"]: float(r["price"]) for r in cur.fetchall()}


def _factors(conn, entity_id: int) -> dict:
    """{ex_date: factor} of one entity: the Bloomberg price adjustment
    factors, the action types of a date multiplied together. Empty for
    an entity that has none."""
    cur = conn.cursor()
    cur.execute(
        "SELECT date, EXP(SUM(LN(factor))) AS factor FROM fact_adjustment_factors "
        "WHERE entity_id = %s AND source = 'bloomberg' GROUP BY date",
        (entity_id,))
    return {r["date"]: float(r["factor"]) for r in cur.fetchall()}


def _convert(prices: dict, fx: dict) -> dict:
    """Constituent priced in the index currency: px * fx, forward-filling
    the FX leg onto the price dates. Dates before the first FX quote
    are dropped (nothing to convert with)."""
    fx_dates = sorted(fx)
    out, i, last_fx = {}, 0, None
    for d in sorted(prices):
        while i < len(fx_dates) and fx_dates[i] <= d:
            last_fx = fx[fx_dates[i]]
            i += 1
        if last_fx is not None:
            out[d] = prices[d] * last_fx
    return out


def build_periods(conn, portfolio_id: int) -> list[dict]:
    """
    Governing versions -> the plain structures chain() consumes, with
    the FX leg folded into each constituent's prices and fixed-weights
    versions expanded into their re-strike periods.
    """
    versions = definitions.governing_versions(conn, portfolio_id)
    if not versions:
        raise ValueError(f"Benchmark portfolio_id={portfolio_id} has no versions.")
    for v in versions:
        if v["method"] == "heuristic":
            raise ValueError(
                f"Version {v['version_id']} uses method 'heuristic', which has "
                "no materialization rule yet; nothing was written.")

    cache: dict[int, dict] = {}

    def prices_of(series_id):
        if series_id not in cache:
            cache[series_id] = _prices(conn, series_id)
        return cache[series_id]

    factor_cache: dict[int, dict] = {}

    def factors_of(entity_id):
        if entity_id not in factor_cache:
            factor_cache[entity_id] = _factors(conn, entity_id)
        return factor_cache[entity_id]

    periods = []
    for v in versions:
        constituents = []
        for c in definitions.constituents(conn, v["version_id"]):
            px = prices_of(c["series_id"])
            if c["fx_series_id"] is not None:
                px = _convert(px, prices_of(c["fx_series_id"]))
            constituents.append({
                "label": c["label"], "weight": float(c["weight"]),
                "prices": px, "factors": factors_of(c["entity_id"]),
                "series_id": c["series_id"], "entity_id": c["entity_id"],
            })
        periods.append({
            "from": v["effective_from"], "version_id": v["version_id"],
            "method": v["method"],
            "rebalance_frequency": v["rebalance_frequency"],
            "constituents": constituents,
        })
    return expand_fixed_weights(periods)


def _anchor(conn, series_id: int, day) -> tuple:
    """Stored level at or before `day`: (date, level), or (None, None)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT date, price FROM fact_prices WHERE series_id = %s AND date <= %s "
        "ORDER BY date DESC LIMIT 1", (series_id, day))
    row = cur.fetchone()
    return (row["date"], float(row["price"])) if row else (None, None)


def materialize(conn, portfolio_id: int, from_date=None) -> dict:
    """
    Regenerates one benchmark's facts from its definition versions.

    from_date=None rebuilds the whole chain from the first version
    (base 100). With a date, the periods from the one governing that
    date onward are rebuilt, anchored at the level already stored for
    that date (regenerate from the changed version's effective_from
    forward; earlier rows stay) - the result equals a full rebuild
    because the stored dates are the union of constituent dates, so
    the carried level is exactly what the old basket would mark there.

    Returns a summary: dates, levels written/unchanged, holdings rows
    written, valuation rows written, stale level dates left outside
    the rebuilt range, first/last date, final level.
    """
    bench = definitions.benchmark(conn, portfolio_id)
    if bench is None:
        raise ValueError(f"portfolio_id={portfolio_id} is not a benchmark portfolio.")
    series_id = bench["series_id"]

    periods = build_periods(conn, portfolio_id)
    base = BASE_LEVEL
    if from_date is not None:
        # The period governing from_date opens the rebuild, struck at
        # the stored level there.
        keep = [p for p in periods if p["from"] <= from_date]
        if not keep:
            raise ValueError(
                f"No version governs {from_date}: the first effective date is "
                f"{periods[0]['from']}.")
        first = keep[-1]
        anchor_date, anchor = _anchor(conn, series_id, first["from"])
        if anchor is None:
            raise ValueError(
                f"No stored level at or before {first['from']} to anchor the "
                "partial rebuild on; run without from_date.")
        base = anchor
        periods = [first] + [p for p in periods if p["from"] > first["from"]]
        logger.info(
            f"{bench['procode']}: partial rebuild from {first['from']} anchored "
            f"at {anchor:.6f} (stored {anchor_date}).")

    rows = chain(periods, base)
    if not rows:
        raise ValueError(f"{bench['procode']}: the chain produced no dates.")

    cur = conn.cursor()
    levels_written = levels_unchanged = holdings_written = valuation_written = 0
    for r in rows:
        day, level = r["date"], float(r["level"])
        cur.execute(UPSERT_LEVEL, (series_id, day, level, SOURCE_MODEL))
        if cur.rowcount > 0:
            levels_written += 1
        else:
            levels_unchanged += 1
        cur.execute(UPSERT_VALUATION, (portfolio_id, day, SOURCE_MODEL, level, level))
        valuation_written += cur.rowcount
        version_id = r["period"]["version_id"]
        for h in r["holdings"]:
            cur.execute(UPSERT_HOLDING, (
                portfolio_id, day, h["constituent"]["entity_id"], version_id,
                SOURCE_MODEL, float(h["units"]), float(h["price"]),
                float(h["market_value"]), float(h["weight"])))
            holdings_written += cur.rowcount

    first_day, last_day = rows[0]["date"], rows[-1]["date"]
    # Stored levels the rebuild did not reach: after the last date, and
    # - on a full rebuild only - before the first effective date.
    if from_date is None:
        cur.execute(
            "SELECT COUNT(*) AS n FROM fact_prices WHERE series_id = %s "
            "AND (date < %s OR date > %s)", (series_id, first_day, last_day))
    else:
        cur.execute(
            "SELECT COUNT(*) AS n FROM fact_prices WHERE series_id = %s AND date > %s",
            (series_id, last_day))
    stale = int(cur.fetchone()["n"])
    update_series_run_metadata(conn, series_id, "success", last_day)

    summary = {
        "procode": bench["procode"], "portfolio_id": portfolio_id,
        "series_id": series_id, "versions": len({p["version_id"] for p in periods}),
        "periods": len(periods), "dates": len(rows),
        "from": first_day, "to": last_day, "level": round(rows[-1]["level"], 6),
        "levels_written": levels_written, "levels_unchanged": levels_unchanged,
        "holdings_written": holdings_written,
        "valuation_written": valuation_written, "stale_levels": stale,
    }
    logger.info(
        f"{bench['procode']}: {summary['dates']} dates {first_day}..{last_day}, "
        f"level {summary['level']:,.4f}; levels {levels_written} written, "
        f"{levels_unchanged} unchanged; holdings {holdings_written} written; "
        f"valuation {valuation_written} written; {stale} stale level(s) outside "
        "the rebuilt range left in place.")
    if stale:
        logger.warning(
            f"{bench['procode']}: {stale} stored level(s) fall outside "
            f"{first_day}..{last_day}; the materializer never deletes - review "
            "them by hand if the first effective date moved.")
    return summary
