# src/pipelines/prices/sbs/valor_cuota/transform.py
# ---------------------------------------------------------------
# Pure transform: staging rows -> fact_prices rows. No DB access.
#
# Each staged (afp, fondo, date) row fans out into up to three fact
# rows, one per metric with a value, resolved against series_registry
# through the (procode, field) map the caller provides.
#
# The wide-table problems the monitor had to solve (filling gaps,
# empty cells clobbering data) do not exist here: in long format a
# missing metric is a row that is not inserted, and a later scrape
# that brings it is a plain INSERT on a new (series_id, date).
# ---------------------------------------------------------------

import logging

import pandas as pd

from src.pipelines.prices.sbs.valor_cuota.afps import METRIC_FIELD, SOURCE_SBS, procode

logger = logging.getLogger(__name__)

# staging column -> business metric name
STG_METRIC = {
    "valor_cuota": "valor_cuota",
    "cuotas": "cuotas",
    "fondo_soles": "fondo",
}

FACT_COLUMNS = ["series_id", "date", "value", "source"]


def transform(stg_df: pd.DataFrame,
              series_map: dict[tuple[str, str], dict]) -> pd.DataFrame:
    """
    Maps staging rows to fact rows [series_id, date, value, source].

    series_map: (procode, field) -> series row, from afps.series_map().
    Rows whose (afp, fondo) resolve to no registered series are dropped
    with a warning - config/afps.yaml is the contract, and a key the
    registry does not know should have been caught at staging.
    """
    if stg_df.empty:
        return pd.DataFrame(columns=FACT_COLUMNS)

    fact_rows = []
    unresolved: set[str] = set()

    # Column-wise zip instead of iterrows: the historical XLS brings
    # ~100k staged rows through here inside a request the user waits on,
    # and materializing a Series per row costs ~10x for nothing.
    columns = [stg_df["afp"], stg_df["fondo"], stg_df["date"]]
    columns += [stg_df[c] for c in STG_METRIC]
    for afp, fund, day, *metrics in zip(*columns):
        try:
            code = procode(afp, int(fund))
        except ValueError:
            unresolved.add(f"{afp}_f{fund}")
            continue
        for (stg_col, metric), val in zip(STG_METRIC.items(), metrics):
            if val is None or pd.isna(val):
                continue
            series = series_map.get((code, METRIC_FIELD[metric]))
            if series is None:
                unresolved.add(f"{code}:{METRIC_FIELD[metric]}")
                continue
            fact_rows.append({
                "series_id": series["series_id"],
                "date": day,
                "value": float(val),
                "source": SOURCE_SBS,
            })

    if unresolved:
        logger.warning(f"valor_cuota transform: no registered series for "
                       f"{', '.join(sorted(unresolved))}")

    facts_df = (pd.DataFrame(fact_rows) if fact_rows
                else pd.DataFrame(columns=FACT_COLUMNS))
    logger.info(f"valor_cuota transform: {len(facts_df)} fact rows.")
    return facts_df
