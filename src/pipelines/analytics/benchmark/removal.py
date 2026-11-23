# src/pipelines/analytics/benchmark/removal.py
# ---------------------------------------------------------------------------
# Removing a benchmark whole. The definitions writer is append-only by
# contract (a version is never updated or deleted: test_definitions guards
# it), so the one operation that does delete lives here, apart, and takes
# the whole benchmark with it - never a version on its own.
# ---------------------------------------------------------------------------

from __future__ import annotations

import logging

from .definitions import PORTFOLIO_TYPE, benchmark

logger = logging.getLogger(__name__)


def delete_benchmark(conn, portfolio_id: int) -> dict:
    """
    Removes a benchmark and everything that stands on it: the fund link,
    the materialized holdings and valuation, the versions with their
    constituents, the level series with its prices, the portfolio and the
    index entity. Nothing else references these rows (the entity is the
    benchmark's own, created by ensure_benchmark), so the removal is
    complete and leaves no orphan. Returns the rows removed per table.
    The caller commits.
    """
    bench = benchmark(conn, portfolio_id)
    if bench is None or bench["portfolio_type"] != PORTFOLIO_TYPE:
        raise LookupError(f"No benchmark with portfolio_id {portfolio_id}.")
    pid, eid, sid = bench["portfolio_id"], bench["entity_id"], bench["series_id"]
    cur = conn.cursor()
    steps = [
        ("benchmark_assignment", "DELETE FROM benchmark_assignment WHERE portfolio_id = %s", pid),
        ("fact_positions_model", "DELETE FROM fact_positions_model WHERE portfolio_id = %s", pid),
        ("fact_portfolio_valuation", "DELETE FROM fact_portfolio_valuation WHERE portfolio_id = %s", pid),
        ("benchmark_constituent",
         "DELETE FROM benchmark_constituent WHERE version_id IN "
         "(SELECT version_id FROM dim_benchmark_version WHERE portfolio_id = %s)", pid),
        ("dim_benchmark_version", "DELETE FROM dim_benchmark_version WHERE portfolio_id = %s", pid),
        ("fact_prices", "DELETE FROM fact_prices WHERE series_id = %s", sid),
        ("series_registry", "DELETE FROM series_registry WHERE series_id = %s", sid),
        ("dim_portfolio", "DELETE FROM dim_portfolio WHERE portfolio_id = %s", pid),
        ("dim_entity", "DELETE FROM dim_entity WHERE entity_id = %s AND entity_type = 'index'", eid),
    ]
    removed: dict[str, int] = {}
    for table, sql, key in steps:
        if key is None:
            removed[table] = 0
            continue
        cur.execute(sql, (key,))
        removed[table] = cur.rowcount
    logger.info(f"benchmark {bench['procode']} (portfolio_id={pid}) removed: {removed}")
    return {"procode": bench["procode"], "display_name": bench["display_name"], "removed": removed}
