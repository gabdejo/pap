# src/pipelines/prices/sbs/valor_cuota/loader.py
# ---------------------------------------------------------------
# Loads valor cuota rows into fact_prices, and owns the ONE pair of
# fact_prices upsert statements every SPP write path shares (daily
# load, manual registration, benchmark, migration). The SQL lives
# here once so a change to the conflict clause or source semantics
# cannot drift between writers.
#
# Default is ON CONFLICT DO NOTHING: re-running never duplicates and
# never overwrites. refresh=True switches to DO UPDATE for the case
# where the SBS restates a published figure - overwriting corrects
# numbers, and in long format there is no way for it to erase one
# (a missing value is a row that never reaches the loader).
# ---------------------------------------------------------------

import logging

import pandas as pd

logger = logging.getLogger(__name__)

UPSERT_NADA = """
    INSERT INTO fact_prices (series_id, date, price, source)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (series_id, date) DO NOTHING
"""
# The WHERE is not an optimization: without it PostgreSQL reports rowcount
# 1 for every conflicting row, identical value included, so a re-scrape
# with refresh reported "336 observaciones nuevas" when it had changed
# nothing - and quietly rewrote each row's `source`, stamping 'sbs' over
# hand corrections marked 'manual'. Unchanged rows now count as skipped
# and keep their provenance.
UPSERT_CORRIGE = """
    INSERT INTO fact_prices (series_id, date, price, source)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (series_id, date) DO UPDATE SET
        price = EXCLUDED.price,
        source = EXCLUDED.source
    WHERE fact_prices.price IS DISTINCT FROM EXCLUDED.price
"""

# Above this many rows the per-row convention (which exists so an error
# names the offending row) costs more than it informs: the historical
# XLS brings ~100k values and one round trip per value dominates the
# load. executemany keeps the same statement and the aggregate counts.
LOTE_MINIMO = 1000


def upsert_fact(conn, series_id: int, fecha, valor: float, source: str,
                refresh: bool = False) -> bool:
    """One fact row through the shared statement. True when written."""
    cur = conn.execute(UPSERT_CORRIGE if refresh else UPSERT_NADA,
                       (int(series_id), fecha, float(valor), source))
    return cur.rowcount > 0


def load_facts(conn, df: pd.DataFrame, refresh: bool = False) -> tuple[int, int]:
    """Returns (loaded, skipped_or_updated)."""
    if df.empty:
        return 0, 0
    stmt = UPSERT_CORRIGE if refresh else UPSERT_NADA
    params = [(int(r.series_id), r.date, float(r.value), r.source)
              for r in df.itertuples(index=False)]

    if len(params) >= LOTE_MINIMO:
        cur = conn.cursor()
        cur.executemany(stmt, params)
        loaded = cur.rowcount if cur.rowcount >= 0 else 0
        other = len(params) - loaded
    else:
        loaded = other = 0
        for p in params:
            cur = conn.execute(stmt, p)
            if cur.rowcount > 0:
                loaded += 1
            else:
                other += 1
    logger.info(
        f"fact_prices (valor_cuota): {loaded} "
        f"{'written or corrected' if refresh else 'loaded'}, "
        f"{other} skipped (already identical).")
    return loaded, other
