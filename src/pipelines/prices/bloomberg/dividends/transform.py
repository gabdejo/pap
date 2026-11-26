# src/pipelines/prices/bloomberg/dividends/transform.py
# ---------------------------------------------------------------
# Pure transform: Bloomberg factor rows -> fact_adjustment_factors shape.
#
#   - parsekyable -> entity_id through the map run.py built
#   - the vendor's operator is folded into ONE convention: multiply
#     prices strictly before `date` by `factor`
#         operator 1 (divide)   -> 1 / factor
#         operator 2 (multiply) -> factor
#         operator 3 (add)      -> DROPPED, logged as an error. An
#           additive adjustment has no multiplicative equivalent, so that
#           security's adjusted series is wrong until it is handled. None
#           seen in the names checked; the view would need a new shape.
#   - several events of the same type on the same date collapse to their
#     product (the fact table holds one row per entity/date/type)
#
# action_type is the CORPORATE_ACTIONS_FILTER value the row came under.
# ---------------------------------------------------------------

import logging

import pandas as pd

logger = logging.getLogger(__name__)

SOURCE = "bloomberg"
COLUMNS = ["entity_id", "date", "action_type", "factor", "source"]


def transform_factors(raw_df: pd.DataFrame, entity_by_ticker: dict[str, int]) -> pd.DataFrame:
    """Returns [entity_id, date, action_type, factor, source]."""
    if raw_df.empty:
        return pd.DataFrame(columns=COLUMNS)

    df = raw_df.copy()
    df["entity_id"] = df["parsekyable"].map(entity_by_ticker)
    unmapped = df["entity_id"].isna().sum()
    if unmapped:
        logger.warning(f"{unmapped} factor rows for tickers outside the requested universe dropped.")
    df = df[df["entity_id"].notna()]

    additive = df[df["operator_type"] == 3]
    if not additive.empty:
        logger.error(
            f"{len(additive)} ADDITIVE factors dropped (no multiplicative equivalent); "
            f"adjusted prices are wrong for: {sorted(additive['parsekyable'].unique())}"
        )
    unknown = df[~df["operator_type"].isin([1, 2, 3])]
    if not unknown.empty:
        logger.error(f"{len(unknown)} rows with unknown operator types "
                     f"{sorted(unknown['operator_type'].unique())} dropped.")
    df = df[df["operator_type"].isin([1, 2])]

    bad = ~(df["factor"] > 0)   # also catches NaN
    if bad.any():
        logger.error(f"{bad.sum()} rows with a non-positive or missing factor dropped.")
    df = df[~bad].copy()
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)

    df["factor"] = df["factor"].where(df["operator_type"] == 2, 1 / df["factor"])
    df["entity_id"] = df["entity_id"].astype(int)

    out = (
        df.groupby(["entity_id", "date", "action_filter"], as_index=False)["factor"].prod()
        .rename(columns={"action_filter": "action_type"})
        .assign(source=SOURCE)
    )
    logger.info(f"dividends transform: {len(out)} factors for {out['entity_id'].nunique()} entities.")
    return out[COLUMNS]
