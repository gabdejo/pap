# src/pipelines/prices/bloomberg/dividends/extract.py
# ---------------------------------------------------------------
# Pulls Bloomberg's own price adjustment factors (bulk reference field
# EQY_DVD_ADJUST_FACT) and stages them in stg_prices_bloomberg_dividends.
#
# The bulk returns a security's WHOLE factor history on every call, and
# only for the action type named by the CORPORATE_ACTIONS_FILTER
# override (its default is CAPITAL_CHANGE alone). So: one request per
# (filter value, chunk of tickers), three filter values, no date window.
#
# A chunk that fails is logged and skipped; the loader only reconciles
# (entity, action type) pairs that came back, so a skipped chunk leaves
# the stored factors untouched instead of deleting them.
#
# Bulk sub-fields as the Terminal names them (verified 2026-11-26):
#   Adjustment Date, Adjustment Factor, Operator Type, Flag
# ---------------------------------------------------------------

import logging

import pandas as pd

from src.vendors.bloomberg import data_request, fetch_bulk_request

logger = logging.getLogger(__name__)

FIELD = "EQY_DVD_ADJUST_FACT"
FILTER_OVERRIDE = "CORPORATE_ACTIONS_FILTER"
ACTION_FILTERS = ("CAPITAL_CHANGE", "ABNORMAL_CASH", "NORMAL_CASH")
CHUNK_SIZE = 50

COLUMNS = ["parsekyable", "action_filter", "date", "factor", "operator_type", "flag"]
_RENAME = {
    "id": "parsekyable",
    "Adjustment Date": "date",
    "Adjustment Factor": "factor",
    "Operator Type": "operator_type",
    "Flag": "flag",
}


def extract_factors(parsekyables: list[str]) -> pd.DataFrame:
    """
    Returns every factor row for the given tickers, all three action
    types: [parsekyable, action_filter, date, factor, operator_type, flag].
    """
    frames = []
    chunks = [parsekyables[i:i + CHUNK_SIZE] for i in range(0, len(parsekyables), CHUNK_SIZE)]
    failed = 0

    for action_filter in ACTION_FILTERS:
        for chunk in chunks:
            try:
                df = fetch_bulk_request(data_request(
                    securities=chunk,
                    fields=[FIELD],
                    options={"overrides": [{"fieldId": FILTER_OVERRIDE, "value": action_filter}]},
                ))
            except Exception as e:
                failed += 1
                logger.error(f"{FIELD} {action_filter}: chunk of {len(chunk)} failed: {e}", exc_info=True)
                continue
            if not df.empty:
                frames.append(_normalize(df, action_filter))

    if failed:
        logger.warning(f"{failed} of {len(chunks) * len(ACTION_FILTERS)} requests failed and were skipped.")
    if not frames:
        return pd.DataFrame(columns=COLUMNS)

    result = pd.concat(frames, ignore_index=True)
    logger.info(
        f"Extracted {len(result)} factor rows for {result['parsekyable'].nunique()} "
        f"of {len(parsekyables)} tickers."
    )
    return result


def _normalize(df: pd.DataFrame, action_filter: str) -> pd.DataFrame:
    """Bloomberg's sub-field names -> staging column names and types."""
    missing = set(_RENAME) - set(df.columns)
    if missing:
        raise ValueError(f"{FIELD} response lacks sub-fields {sorted(missing)}; got {list(df.columns)}")
    df = df.rename(columns=_RENAME).assign(action_filter=action_filter)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.astype({"factor": float, "operator_type": int, "flag": int})
    return df[COLUMNS]


def load_stg_dividends(conn, df: pd.DataFrame) -> int:
    """
    Stages the rows not seen before (the UNIQUE key is the whole row), so
    the daily re-pull of full history adds nothing unless Bloomberg added
    or revised a factor. Returns the number of new rows.
    """
    inserted = 0
    for _, row in df.iterrows():
        cur = conn.execute(
            """
            INSERT INTO stg_prices_bloomberg_dividends
                (parsekyable, action_filter, date, factor, operator_type, flag)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (parsekyable, action_filter, date, factor, operator_type, flag)
            DO NOTHING
            """,
            (
                row["parsekyable"],
                row["action_filter"],
                row["date"],
                float(row["factor"]),
                int(row["operator_type"]),
                int(row["flag"]),
            ),
        )
        inserted += cur.rowcount

    logger.info(f"stg_prices_bloomberg_dividends: {inserted} new rows of {len(df)} pulled.")
    return inserted
