# src/pipelines/prices/sbs/valor_cuota/loader.py
# ---------------------------------------------------------------
# Loads valor cuota rows into fact_prices, and owns the ONE pair of
# fact_prices statements every SPP write path shares (daily load,
# history load, manual registration, history migration). The SQL
# lives here once so a change to the conflict clause or the source
# semantics cannot drift between writers.
#
# Default is ON CONFLICT DO NOTHING: re-running never duplicates and
# never overwrites - a hand correction (source 'manual') survives the
# next scrape. force=True switches to DO UPDATE for a restatement,
# the same replace semantics the other SBS feeds use: a changed value
# is rewritten, an identical one is left alone, and in long format
# nothing can ERASE a value (a missing figure is a row that never
# reaches the loader).
# ---------------------------------------------------------------

import logging

import pandas as pd

logger = logging.getLogger(__name__)

INSERT_KEEP = """
    INSERT INTO fact_prices (series_id, date, price, source)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (series_id, date) DO NOTHING
"""
# The WHERE is not an optimization: without it PostgreSQL reports
# rowcount 1 for every conflicting row, identical value included, so a
# forced re-scrape reported hundreds of "new" rows when it had changed
# nothing - and quietly rewrote each row's `source`, stamping 'sbs' over
# hand corrections marked 'manual'. Unchanged rows now count as
# unchanged and keep their provenance.
UPSERT_REPLACE = """
    INSERT INTO fact_prices (series_id, date, price, source)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (series_id, date) DO UPDATE SET
        price = EXCLUDED.price,
        source = EXCLUDED.source
    WHERE fact_prices.price IS DISTINCT FROM EXCLUDED.price
"""


def upsert_fact(conn, series_id: int, day, value: float, source: str,
                force: bool = False) -> bool:
    """One fact row through the shared statement. True when written."""
    cur = conn.cursor()
    cur.execute(UPSERT_REPLACE if force else INSERT_KEEP,
                (int(series_id), day, float(value), source))
    return cur.rowcount > 0


def load_facts(conn, df: pd.DataFrame, force: bool = False) -> tuple[int, int]:
    """
    Row-by-row load (project convention: an error names the offending
    row). Returns (written, skipped): skipped rows already existed and,
    with force, were identical.
    """
    if df.empty:
        return 0, 0
    stmt = UPSERT_REPLACE if force else INSERT_KEEP
    cur = conn.cursor()
    written = skipped = 0
    for _, row in df.iterrows():
        cur.execute(stmt, (int(row["series_id"]), row["date"],
                           float(row["value"]), row["source"]))
        if cur.rowcount > 0:
            written += 1
        else:
            skipped += 1
    logger.info(
        f"fact_prices (valor_cuota): {written} {'written' if force else 'loaded'}, "
        f"{skipped} {'unchanged' if force else 'already present'}.")
    return written, skipped
