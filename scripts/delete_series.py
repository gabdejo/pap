# scripts/delete_series.py
# ---------------------------------------------------------------
# Maintenance script: completely removes one or more series and
# optionally their parent entity from the database.
#
# Deletes in FK dependency order:
#   1. fact_prices rows
#   2. series_registry rows
#   3. stg_* Bloomberg staging rows (prices, dividends, security)
#   4. fact_adjustment_factors rows      (if --delete-entity)
#   5. dim_entity_identifiers rows       (if --delete-entity)
#   6. dim_security / extension tables   (if --delete-entity)
#   7. dim_entity row                    (if --delete-entity)
#
# Positions, contribution, benchmark constituents/assignments and
# dim_portfolio.parent_entity_id are deliberately NOT cleaned: if the
# entity is referenced there the FK rejects the delete and the whole
# procode rolls back. An entity that is held or sits in a benchmark
# should not be deleted.
#
# Only hand-curated entities (series.csv, the tablero) are accepted. An
# entity carrying a codigo_sbs identifier is refused: nothing records
# how a row was created, and that identifier is what marks it as part
# of the SBS universe, which the pipelines would register again.
#
# USE WITH CARE - destructive and irreversible.
# Always run --dry-run first to verify scope.
#
# Usage:
#   # Dry run first - see what would be deleted
#   python scripts/delete_series.py --procode AAPL --dry-run
#
#   # Delete all series for a procode (keeps dim_entity)
#   python scripts/delete_series.py --procode AAPL
#
#   # Delete a specific field/source combination only
#   python scripts/delete_series.py --procode AAPL --field PX_LAST --source bloomberg
#
#   # Delete series AND the parent entity + all dim rows
#   python scripts/delete_series.py --procode AAPL --delete-entity
#
#   # Delete multiple procodes at once
#   python scripts/delete_series.py --procode AAPL MSFT GOOGL --dry-run
#   python scripts/delete_series.py --procode AAPL MSFT GOOGL --delete-entity
# ---------------------------------------------------------------

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.connection import get_connection
from src.shared.logging import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete series and optionally entity from the database.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--procode",
        nargs="+",
        required=True,
        help="One or more procodes (dim_entity.procode) to delete (e.g. AAPL MSFT).",
    )
    parser.add_argument(
        "--field",
        type=str,
        default=None,
        help=(
            "Filter to a specific field (e.g. PX_LAST). "
            "Deletes all fields if omitted."
        ),
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help=(
            "Filter to a specific source (e.g. bloomberg). "
            "Deletes all sources if omitted."
        ),
    )
    parser.add_argument(
        "--delete-entity",
        action="store_true",
        default=False,
        dest="delete_entity",
        help=(
            "Also delete the parent entity and all associated dim rows "
            "(dim_entity, dim_security, dim_macro, dim_entity_identifiers, "
            "dim_source_priority, extension tables). "
            "Only safe when ALL series for the entity are being deleted."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Show what would be deleted without making any changes.",
    )

    args = parser.parse_args()
    setup_logging("delete_series")

    for procode in args.procode:
        _delete_procode(
            procode=procode,
            field=args.field,
            source=args.source,
            delete_entity=args.delete_entity,
            dry_run=args.dry_run,
        )

    if args.dry_run:
        logger.info("Dry run complete. No changes made.")
    else:
        logger.info("Deletion complete.")


# ---- Per-procode deletion ---------------------------------------

def _delete_procode(
    procode: str,
    field: str | None,
    source: str | None,
    delete_entity: bool,
    dry_run: bool,
) -> None:

    with get_connection() as conn:

        # Resolve entity
        entity = conn.execute(
            "SELECT entity_id, entity_type FROM dim_entity WHERE procode = %s",
            (procode,),
        ).fetchone()

        if not entity:
            logger.warning(f"Procode '{procode}' not found in dim_entity. Skipping.")
            return

        entity_id   = entity["entity_id"]
        entity_type = entity["entity_type"]

        # Only hand-curated entities. One with a codigo_sbs belongs to the
        # SBS universe (discovered there, or a seed that adopted it by ISIN):
        # the SBS run or the held rule would register it again under a new
        # entity_id, with its history gone.
        if conn.execute(
            "SELECT 1 FROM dim_entity_identifiers "
            "WHERE entity_id = %s AND id_type = 'codigo_sbs' LIMIT 1",
            (entity_id,),
        ).fetchone():
            logger.warning(
                f"Procode '{procode}' (entity_id={entity_id}) has a codigo_sbs: "
                f"it is part of the SBS universe, not a curated series. Skipping."
            )
            return

        # Resolve series to delete
        query  = "SELECT series_id, field, domain, source FROM series_registry WHERE entity_id = %s"
        params = [entity_id]

        if field:
            query  += " AND field = %s"
            params.append(field)
        if source:
            query  += " AND source = %s"
            params.append(source)

        series_rows = conn.execute(query, params).fetchall()

        if not series_rows:
            logger.warning(
                f"No series found for procode='{procode}' "
                f"field={field or 'any'} source={source or 'any'}. Skipping."
            )
            return

        series_ids = [r["series_id"] for r in series_rows]

        logger.info(
            f"{'[DRY RUN] ' if dry_run else ''}"
            f"procode='{procode}' entity_id={entity_id} "
            f"entity_type={entity_type} | "
            f"{len(series_ids)} series to delete"
        )
        for sr in series_rows:
            logger.info(
                f"  series_id={sr['series_id']} "
                f"field={sr['field']} "
                f"domain={sr['domain']} "
                f"source={sr['source']}"
            )

        # Count fact rows
        cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM fact_prices WHERE series_id = ANY(%s)",
            (series_ids,),
        ).fetchone()["n"]
        logger.info(f"  fact_prices: {cnt:,} rows will be deleted")

        if dry_run:
            return

        # 1. Fact tables
        deleted = conn.execute(
            "DELETE FROM fact_prices WHERE series_id = ANY(%s)",
            (series_ids,),
        ).rowcount
        logger.info(f"  Deleted {deleted:,} rows from fact_prices.")

        # 2. series_registry
        deleted = conn.execute(
            "DELETE FROM series_registry WHERE series_id = ANY(%s)",
            (series_ids,),
        ).rowcount
        logger.info(f"  Deleted {deleted} rows from series_registry.")

        # 5. Staging tables (keyed by bloomberg_ticker, resolve first)
        bbg_ticker_row = conn.execute(
            """
            SELECT id_value FROM dim_entity_identifiers
            WHERE entity_id = %s AND id_type = 'parsekyable'
            """,
            (entity_id,),
        ).fetchone()

        if bbg_ticker_row:
            bbg_ticker = bbg_ticker_row["id_value"]
            for stg_table in (
                "stg_prices_bloomberg",
                "stg_prices_bloomberg_dividends",
                "stg_security_bloomberg",
            ):
                deleted = conn.execute(
                    f"DELETE FROM {stg_table} WHERE parsekyable = %s",
                    (bbg_ticker,),
                ).rowcount
                if deleted:
                    logger.info(f"  Deleted {deleted:,} rows from {stg_table}.")

        if not delete_entity:
            logger.info(
                f"Series deleted for '{procode}'. "
                f"Entity and dim rows retained (use --delete-entity to remove)."
            )
            return

        # Check no remaining series exist for this entity before
        # deleting entity-level rows
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM series_registry WHERE entity_id = %s",
            (entity_id,),
        ).fetchone()["n"]

        if remaining > 0:
            logger.warning(
                f"'{procode}' still has {remaining} series in series_registry "
                f"after deletion (from other field/source combinations). "
                f"Skipping entity deletion to preserve referential integrity. "
                f"Delete all series first or add --field / --source filters."
            )
            return

        # Adjustment factors hang off the entity, not a series
        conn.execute(
            "DELETE FROM fact_adjustment_factors WHERE entity_id = %s",
            (entity_id,),
        )

        # 6. dim_entity_identifiers
        deleted = conn.execute(
            "DELETE FROM dim_entity_identifiers WHERE entity_id = %s",
            (entity_id,),
        ).rowcount
        logger.info(f"  Deleted {deleted} rows from dim_entity_identifiers.")

        # 7. Extension tables and dim_security / dim_macro
        if entity_type == "security":
            sec_row = conn.execute(
                "SELECT security_id FROM dim_security WHERE entity_id = %s",
                (entity_id,),
            ).fetchone()

            if sec_row:
                security_id = sec_row["security_id"]
                for ext_table in (
                    "dim_security_equity",
                    "dim_security_fund",
                    "dim_security_bond",
                ):
                    conn.execute(
                        f"DELETE FROM {ext_table} WHERE security_id = %s",
                        (security_id,),
                    )

                conn.execute(
                    "DELETE FROM dim_security WHERE entity_id = %s",
                    (entity_id,),
                )
                logger.info(f"  Deleted dim_security and extension rows.")

        # 8. dim_entity (last - everything else references it)
        conn.execute(
            "DELETE FROM dim_entity WHERE entity_id = %s",
            (entity_id,),
        )
        logger.info(f"  Deleted dim_entity row for '{procode}' (entity_id={entity_id}).")
        logger.info(f"Entity '{procode}' fully removed from database.")


if __name__ == "__main__":
    main()
