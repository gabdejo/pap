# scripts/delete_benchmark.py
# ---------------------------------------------------------------
# Maintenance script: completely removes one or more benchmarks
# (composite indices, dim_portfolio source 'model' / portfolio_type
# 'benchmark') - definition, materialized facts and level series.
#
# Deletes in FK dependency order, one transaction per procode:
#   1. fact_positions_model, fact_portfolio_valuation   (materialized)
#   2. benchmark_constituent, dim_benchmark_version     (definition)
#   3. benchmark_assignment
#   4. dim_portfolio row
#   5. fact_prices + series_registry of the 'model' level series
#   6. dim_entity_identifiers + dim_entity (the 'index' entity), only
#      if no other series hangs off it - ensure_benchmark() adopts an
#      existing index entity with the same procode, and that one stays.
#
# This BREAKS the append-only rule of the definition tables on purpose:
# it is for test / mistaken benchmarks. One that business has consumed
# should be retired instead (dim_portfolio.status = 'inactive').
#
# Refused: a benchmark whose level series is a constituent (price or FX
# leg) of another benchmark - version that one out of it first.
# The constituents' own series are NOT touched (see delete_series.py).
#
# USE WITH CARE - destructive and irreversible.
# Always run --dry-run first to verify scope.
#
# Usage:
#   python scripts/delete_benchmark.py --procode CLOWN --dry-run
#   python scripts/delete_benchmark.py --procode CLOWN
#   python scripts/delete_benchmark.py --procode CLOWN TEST_6040
# ---------------------------------------------------------------

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.connection import get_connection
from src.pipelines.analytics.benchmark import definitions
from src.shared.logging import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete benchmarks (definition, facts, level series) from the database.",
    )
    parser.add_argument(
        "--procode", nargs="+", required=True,
        help="One or more benchmark procodes (dim_portfolio.procode, source 'model').",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Show what would be deleted without making any changes.",
    )
    args = parser.parse_args()
    setup_logging("delete_benchmark")

    for procode in args.procode:
        _delete_benchmark(procode, args.dry_run)

    logger.info("Dry run complete. No changes made." if args.dry_run else "Deletion complete.")


def _delete_benchmark(procode: str, dry_run: bool) -> None:
    tag = "[DRY RUN] " if dry_run else ""

    with get_connection() as conn:
        bench = definitions.benchmark_by_procode(conn, procode)
        if bench is None or bench["portfolio_type"] != definitions.PORTFOLIO_TYPE:
            logger.warning(f"'{procode}' is not a benchmark portfolio. Skipping.")
            return

        pid, eid = bench["portfolio_id"], bench["entity_id"]

        # Only the 'model' series: an adopted index entity may carry real ones.
        series_ids = [
            r["series_id"] for r in conn.execute(
                "SELECT series_id FROM series_registry WHERE entity_id = %s AND source = %s",
                (eid, definitions.SOURCE_MODEL),
            ).fetchall()
        ]

        holders = conn.execute(
            """
            SELECT DISTINCT p.procode
            FROM benchmark_constituent c
            JOIN dim_benchmark_version v ON v.version_id = c.version_id
            JOIN dim_portfolio p ON p.portfolio_id = v.portfolio_id
            WHERE (c.series_id = ANY(%s) OR c.fx_series_id = ANY(%s))
              AND v.portfolio_id <> %s
            """,
            (series_ids, series_ids, pid),
        ).fetchall()
        if holders:
            names = ", ".join(r["procode"] for r in holders)
            logger.warning(
                f"'{procode}' is a constituent of other benchmark(s): {names}. Skipping."
            )
            return

        logger.info(
            f"{tag}benchmark '{procode}' ({bench['display_name']}) "
            f"portfolio_id={pid} entity_id={eid} level series={series_ids}"
        )

        steps = [
            ("fact_positions_model", "portfolio_id = %s", pid),
            ("fact_portfolio_valuation", "portfolio_id = %s", pid),
            ("benchmark_constituent",
             "version_id IN (SELECT version_id FROM dim_benchmark_version "
             "WHERE portfolio_id = %s)", pid),
            ("dim_benchmark_version", "portfolio_id = %s", pid),
            ("benchmark_assignment", "portfolio_id = %s", pid),
            ("dim_portfolio", "portfolio_id = %s", pid),
            ("fact_prices", "series_id = ANY(%s)", series_ids),
            ("series_registry", "series_id = ANY(%s)", series_ids),
        ]
        for table, where, param in steps:
            if dry_run:
                n = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", (param,)
                ).fetchone()["n"]
                logger.info(f"  {table}: {n:,} rows would be deleted")
            else:
                n = conn.execute(f"DELETE FROM {table} WHERE {where}", (param,)).rowcount
                logger.info(f"  Deleted {n:,} rows from {table}.")

        if eid is None:
            return

        other = conn.execute(
            "SELECT COUNT(*) AS n FROM series_registry "
            "WHERE entity_id = %s AND series_id <> ALL(%s)",
            (eid, series_ids),
        ).fetchone()["n"]
        if other:
            logger.info(
                f"  dim_entity {eid} kept: {other} non-model series still hang off it."
            )
        elif dry_run:
            logger.info(f"  dim_entity {eid} and its identifiers would be deleted")
        else:
            conn.execute("DELETE FROM dim_entity_identifiers WHERE entity_id = %s", (eid,))
            conn.execute("DELETE FROM dim_entity WHERE entity_id = %s", (eid,))
            logger.info(f"  Deleted dim_entity row (entity_id={eid}).")


if __name__ == "__main__":
    main()
