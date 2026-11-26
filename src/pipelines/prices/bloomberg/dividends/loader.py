# src/pipelines/prices/bloomberg/dividends/loader.py
# ---------------------------------------------------------------
# Reconciles fact_adjustment_factors with the factor set just pulled.
#
# Unlike a price loader this one updates and deletes: every pull is the
# full history, so for each (entity, action type) pair present in the
# pull the stored rows are made to MATCH it -
#   new date        -> insert
#   changed factor  -> update (loaded_at moves)
#   date not pulled -> delete (Bloomberg withdrew or re-dated the event)
#
# Only pairs with at least one pulled row are reconciled. A pair that is
# absent - failed request, or an entity outside this run - is left
# alone, so a partial pull never deletes good factors.
#
# Returns counts plus the earliest date touched: anything stored that
# was derived from the factors from that date on used the old ones.
# Today that is the benchmark materializer (analytics/benchmark), which
# rescales a constituent's units at each ex-date. fact_contribution is
# NOT affected: method 'fms' is built from FMS accounting flows,
# dividends included, and reads no prices.
# ---------------------------------------------------------------

import logging
from datetime import date

import pandas as pd

from src.pipelines.prices.bloomberg.dividends.transform import SOURCE

logger = logging.getLogger(__name__)


def load_factors(conn, df: pd.DataFrame) -> dict:
    """Returns {'inserted', 'updated', 'deleted', 'earliest': date | None}."""
    inserted = updated = deleted = 0
    touched: list[date] = []

    for (entity_id, action_type), group in df.groupby(["entity_id", "action_type"]):
        for _, row in group.iterrows():
            cur = conn.execute(
                """
                INSERT INTO fact_adjustment_factors (entity_id, date, action_type, factor, source)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (entity_id, date, action_type, source) DO UPDATE
                    SET factor = EXCLUDED.factor, loaded_at = CURRENT_TIMESTAMP
                    WHERE fact_adjustment_factors.factor IS DISTINCT FROM EXCLUDED.factor
                RETURNING (xmax = 0) AS is_insert
                """,
                (int(entity_id), row["date"], action_type, float(row["factor"]), SOURCE),
            )
            hit = cur.fetchone()   # None when the stored factor already matches
            if hit is None:
                continue
            if hit["is_insert"]:
                inserted += 1
            else:
                updated += 1
            touched.append(row["date"])

        # ponytail: a pair whose ONLY event is withdrawn returns no rows, is
        # never reconciled, and keeps its stale factor. Needs the list of
        # (ticker, filter) requests that succeeded passed down to fix.
        cur = conn.execute(
            """
            DELETE FROM fact_adjustment_factors
            WHERE entity_id = %s AND action_type = %s AND source = %s
              AND NOT (date = ANY(%s))
            RETURNING date
            """,
            (int(entity_id), action_type, SOURCE, list(group["date"])),
        )
        gone = [r["date"] for r in cur.fetchall()]
        deleted += len(gone)
        touched.extend(gone)

    logger.info(f"fact_adjustment_factors: {inserted} inserted, {updated} updated, {deleted} deleted.")
    return {
        "inserted": inserted,
        "updated": updated,
        "deleted": deleted,
        "earliest": min(touched) if touched else None,
    }
