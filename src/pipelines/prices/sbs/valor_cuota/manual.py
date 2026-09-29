# src/pipelines/prices/sbs/valor_cuota/manual.py
# ---------------------------------------------------------------
# Manual registration and correction of valor cuota values: one AFP,
# one date, all its funds at once, in a single transaction - either
# every fund enters or none does.
#
# Long-format semantics: writing a value is an upsert on (series_id,
# date) with source='manual' - forced, so a hand correction overrides
# the scraped figure and is not overridden back by the next incremental
# scrape (the loader's default keeps what is there). CLEARING a value
# is deleting that row (no NULL cells exist here), and a date with no
# values left is simply a date with no rows: it leaves the book by
# itself, no cleanup pass.
#
# clean_values / write_date are the grammar of the form, kept generic
# (the fund->series resolver is injected) so another writer with the
# same form shape can reuse them without duplicating the validation.
# ---------------------------------------------------------------

import datetime as dt
import logging
from typing import Callable

import pandas as pd

from src.db.connection import get_connection
from src.pipelines.prices.sbs.valor_cuota import afps
from src.pipelines.prices.sbs.valor_cuota.loader import upsert_fact

logger = logging.getLogger(__name__)

SOURCE_MANUAL = "manual"


# ---- Shared grammar of the manual forms -----------------------------

def clean_values(values: dict, validate_fund: Callable[[int], None],
                 label: str) -> dict[int, float | None]:
    """
    Normalizes a {fund: value} form payload: None/'' means delete, a
    number must be positive, and validate_fund raises for funds the
    form must not accept. `label` names the value in the error message.
    """
    cleaned: dict[int, float | None] = {}
    for fund, v in (values or {}).items():
        fund = int(fund)
        validate_fund(fund)
        if v is None or v == "":
            cleaned[fund] = None
        else:
            v = float(v)
            if v <= 0:
                raise ValueError(f"{label} for fund {fund} must be greater than zero.")
            cleaned[fund] = v
    if not cleaned:
        raise ValueError("No values received.")
    return cleaned


def date_exists(conn, series_ids: list[int], day) -> bool:
    """Whether any of the given series has a row on that date."""
    if not series_ids:
        return False
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM fact_prices WHERE date = %s AND series_id = ANY(%s) LIMIT 1",
        (day, series_ids))
    return cur.fetchone() is not None


def write_date(conn, day, cleaned: dict[int, float | None],
               series_of: Callable[[int], int],
               universe_ids: list[int]) -> dict:
    """
    Applies a cleaned form to one date: value -> upsert (source
    'manual'), None -> DELETE. `universe_ids` is the series universe
    that decides whether the date existed before / has anything left
    after. Returns {saved, deleted, new_row, row_deleted}.
    """
    existed = date_exists(conn, universe_ids, day)
    saved, deleted = {}, []
    cur = conn.cursor()
    for fund, v in cleaned.items():
        sid = series_of(fund)
        if v is None:
            cur.execute(
                "DELETE FROM fact_prices WHERE series_id = %s AND date = %s",
                (sid, day))
            if cur.rowcount > 0:
                deleted.append(fund)
        else:
            upsert_fact(conn, sid, day, v, SOURCE_MANUAL, force=True)
            saved[fund] = v
    row_deleted = existed and not date_exists(conn, universe_ids, day)
    return {"saved": saved, "deleted": deleted,
            "new_row": not existed, "row_deleted": bool(row_deleted)}


def validate_date(day) -> dt.date:
    day = pd.to_datetime(day).date()
    if day > dt.date.today():
        raise ValueError("The date cannot be in the future.")
    return day


# ---- Valor cuota form -----------------------------------------------

def register_values(day, afp: str, values: dict,
                    metric: str = "valor_cuota") -> dict:
    """
    Registers or corrects ALL of one AFP's funds on one date.

    `values` is keyed by fund type. None deletes that value; a fund
    absent from the dict is left as is. Everything happens in one
    transaction.
    """
    key = afps.key_of(afp)
    if not key:
        raise ValueError(f"Unregistered AFP: {afp}. Add it to config/afps.yaml.")
    name = afps.name_of(key)
    if metric not in afps.METRIC_FIELD:
        raise ValueError(f"Invalid metric: {metric}")
    day = validate_date(day)

    def validate_fund(fund: int) -> None:
        if fund not in afps.funds():
            raise ValueError(f"Invalid fund type: {fund}")
        if not afps.operates(key, fund):
            raise ValueError(f"{name} does not operate fund {fund} (config/afps.yaml).")

    cleaned = clean_values(values, validate_fund, "The value")
    field = afps.METRIC_FIELD[metric]

    with get_connection() as conn:
        series_map = afps.series_map(conn)

        def series_of(fund: int) -> int:
            s = series_map.get((afps.procode(key, fund), field))
            if s is None:
                raise ValueError(
                    f"No series registered for {name} F{fund} / {metric}. "
                    "Run scripts/run_sbs_valor_cuota.py --register-only.")
            return s["series_id"]

        universe_ids = [s["series_id"] for s in series_map.values()]
        result = write_date(conn, day, cleaned, series_of, universe_ids)

    logger.info(f"manual registration: {name} {metric} {day} - "
                f"{len(result['saved'])} saved, {len(result['deleted'])} deleted.")
    return {"date": str(day), "afp": name, "metric": metric, **result}
