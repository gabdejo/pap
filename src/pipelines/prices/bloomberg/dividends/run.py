# src/pipelines/prices/bloomberg/dividends/run.py
# ---------------------------------------------------------------
# Entry point for the Bloomberg price adjustment factors.
#
# Universe: every entity with an ACTIVE PX_LAST/bloomberg daily series
# whose ticker carries the Equity yellow key (stocks, ETFs, funds).
# Indices, currencies and bonds have no factors and are not asked. No
# series_registry row is needed for the factors themselves, so a name
# gets them the day its price series goes active and stops when the
# held-universe rule suspends it.
#
# Not incremental: each run pulls full history for the whole universe
# and reconciles (see loader.py). That adds no monthly unique securities
# - they are the ones the price run already asks about - and is why a
# correction Bloomberg makes to an old dividend is picked up unprompted.
#
# Runs daily after prices_bloomberg so an ex-date's price and its factor
# land the same evening. A factor that arrives LATER than its ex-date is
# the case to watch: vw_prices_adjusted corrects itself, but anything
# stored from it in between does not. The run logs a warning with the
# earliest date affected.
# ---------------------------------------------------------------

import logging
from datetime import date

from src.db.connection import get_connection
from src.db.queries import get_active_series
from src.pipelines.prices.bloomberg.dividends.extract import extract_factors, load_stg_dividends
from src.pipelines.prices.bloomberg.dividends.loader import load_factors
from src.pipelines.prices.bloomberg.dividends.transform import transform_factors

logger = logging.getLogger(__name__)


def run(run_date: date | None = None, tickers: list[str] | None = None) -> dict | None:
    """
    :param run_date: only used to tell a late factor from today's. Defaults to today.
    :param tickers: restrict the universe to these parsekyables (testing, one-off repair).
    :return: the loader's counts, or None when there was nothing to load.
    """
    run_date = run_date or date.today()
    logger.info(f"=== prices/bloomberg/dividends run started | run_date={run_date} ===")

    with get_connection() as conn:
        series = get_active_series(conn, domain="prices", source="bloomberg", frequency="daily")
    entity_by_ticker = _universe(series, tickers)

    if not entity_by_ticker:
        logger.warning("No equity tickers with an active Bloomberg price series. Exiting.")
        return None
    logger.info(f"Universe: {len(entity_by_ticker)} equity tickers.")

    raw_df = extract_factors(sorted(entity_by_ticker))
    if raw_df.empty:
        logger.warning("Extract returned no factors.")
        return None

    with get_connection() as conn:
        load_stg_dividends(conn, raw_df)

    factors = transform_factors(raw_df, entity_by_ticker)
    if factors.empty:
        logger.warning("Transform returned no factors.")
        return None

    with get_connection() as conn:
        result = load_factors(conn, factors)

    earliest = result["earliest"]
    if earliest is not None and earliest < run_date:
        logger.warning(
            f"Factors dated before {run_date} were added, changed or removed; earliest {earliest}. "
            f"Benchmarks holding the affected names were materialized without them from {earliest} on: "
            f"rebuild those (scripts/run_benchmark.py)."
        )

    logger.info("=== prices/bloomberg/dividends run complete ===")
    return result


def _universe(series: list[dict], tickers: list[str] | None) -> dict[str, int]:
    """{parsekyable: entity_id} for the equity tickers among the active price series."""
    wanted = {t.strip().upper() for t in tickers} if tickers else None
    out = {}
    for s in series:
        ticker = s.get("parsekyable")
        if s["field"] != "PX_LAST" or not ticker or not ticker.upper().endswith(" EQUITY"):
            continue
        if wanted is not None and ticker.upper() not in wanted:
            continue
        out[ticker] = s["entity_id"]
    return out
