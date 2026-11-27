# src/pipelines/analytics/tracking_error/data.py
# ---------------------------------------------------------------
# Database half of the tracking error: readers that take the
# connection (the caller owns it) and return plain dicts / frames.
#
# Asset keys are strings: str(entity_id) for a priced entity,
# 'CCY_<code>' for a currency exposure. FX keys: 'CCY_<code>' (SBS
# tipo_cambio mid, PEN per unit) or 'S<series_id>' (the fx_series_id
# of a benchmark constituent).
#
# Portfolio side (vw_positions_unified, in_nav rows):
#   security                 -> its entity, weight as the view gives it
#   deposit / cash / net CxC -> CCY_<codigo_iso_moneda>
#   forwards (NOT in_nav)    -> +nocional_soles/AUM on CCY_<moneda_compra>,
#                               -nocional_soles/AUM on CCY_<moneda_venta>.
#                               The view's forward weight is MTM, which
#                               hides the hedge; the legs carry it.
#
# Series per held entity: bonds (ind_clase 145) use PX_CLEAN_PCT/sbs
# when it exists - a coupon drop in a dirty price is a false one-day
# loss. Otherwise PX_LAST/bloomberg when active, else PX_LAST/sbs.
# One series per entity, never a mix (see 49_vw_prices_adjusted.sql).
# PX_LAST is read back-adjusted from vw_prices_adjusted; only ratios
# of price_adjusted taken in one read are used, as its header asks.
#
# Currency of a price: dim_security.currency for a Bloomberg series,
# the position's codigo_iso_moneda for an SBS one.
# ---------------------------------------------------------------

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

BASE = "PEN"
IND_CLASE_BOND = 145

# {currency: procode} to force an SBS FX entity; empty = the one with
# the most bid+ask days in the window (docs/TRACKING_ERROR_INPUTS.md A3).
FX_FUENTE: dict[str, str] = {}


def ccy_key(code: str) -> str:
    return f"CCY_{code.strip().upper()}"


def portfolio_row(conn, procode: str, benchmark: bool) -> dict:
    """The dim_portfolio row of a fund, or of a benchmark."""
    cur = conn.cursor()
    cur.execute(
        "SELECT portfolio_id, procode, source FROM dim_portfolio "
        "WHERE procode = %s AND (portfolio_type = 'benchmark') = %s",
        (procode, benchmark))
    rows = cur.fetchall()
    if len(rows) != 1:
        kind = "benchmark" if benchmark else "non-benchmark"
        raise ValueError(f"{procode}: expected one {kind} portfolio, found {len(rows)}.")
    return rows[0]


def portfolio_weights(conn, port: dict, as_of: date) -> tuple[dict, dict]:
    """
    (weights, meta) of a fund on one date. weights: {asset key: weight
    over AUM}. meta, securities only: {key: {entity_id, currency,
    ind_clase}}.
    """
    args = (port["portfolio_id"], as_of, port["source"])
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.position_type, u.security_entity_id, u.codigo_iso_moneda,
               u.weight, s.ind_clase
        FROM vw_positions_unified u
        LEFT JOIN fact_positions_securities s
               ON  s.portfolio_id = u.portfolio_id AND s.date = u.date
               AND s.source = u.source
               AND s.security_entity_id = u.security_entity_id
        WHERE u.portfolio_id = %s AND u.date = %s AND u.source = %s AND u.in_nav
        """, args)
    rows = cur.fetchall()
    if not rows:
        raise ValueError(f"{port['procode']}: no positions on {as_of}.")

    weights: dict[str, float] = {}
    meta: dict[str, dict] = {}
    unvalued = 0
    for r in rows:
        if r["weight"] is None:
            unvalued += 1
            continue
        currency = (r["codigo_iso_moneda"] or BASE).strip().upper()
        if r["position_type"] == "security":
            key = str(r["security_entity_id"])
            meta[key] = {"entity_id": r["security_entity_id"], "currency": currency,
                         "ind_clase": r["ind_clase"]}
        else:
            key = ccy_key(currency)
        weights[key] = weights.get(key, 0.0) + float(r["weight"])
    if unvalued:
        logger.warning(f"{port['procode']} {as_of}: {unvalued} positions without a weight, skipped.")

    cur.execute(
        """
        SELECT f.moneda_compra, f.moneda_venta,
               f.nocional_soles / NULLIF(v.valor_cartera, 0) AS weight
        FROM fact_positions_forwards f
        JOIN fact_portfolio_valuation v USING (portfolio_id, date, source)
        WHERE f.portfolio_id = %s AND f.date = %s AND f.source = %s
        """, args)
    for r in cur.fetchall():
        if r["weight"] is None:
            continue
        for code, sign in ((r["moneda_compra"], 1.0), (r["moneda_venta"], -1.0)):
            key = ccy_key(code)
            weights[key] = weights.get(key, 0.0) + sign * float(r["weight"])
    return weights, meta


def benchmark_weights(conn, portfolio_id: int, as_of: date) -> tuple[dict, dict]:
    """
    (weights, legs) of a benchmark on one date, from its materialized
    holdings. legs: {key: {series_id, adjusted, fx}} - the series that
    prices each constituent and its FX key, as the definition says.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.entity_id, m.weight, bc.series_id, bc.fx_series_id, sr.field
        FROM vw_positions_model_current m
        JOIN benchmark_constituent bc ON bc.version_id = m.version_id
        JOIN series_registry sr
          ON sr.series_id = bc.series_id AND sr.entity_id = m.entity_id
        WHERE m.portfolio_id = %s AND m.date = %s
        """, (portfolio_id, as_of))
    rows = cur.fetchall()
    if not rows:
        raise ValueError(
            f"benchmark {portfolio_id}: no holdings on {as_of}. "
            f"Materialize it (scripts/run_benchmark.py).")
    weights, legs = {}, {}
    for r in rows:
        key = str(r["entity_id"])
        weights[key] = weights.get(key, 0.0) + float(r["weight"])
        legs[key] = {
            "series_id": r["series_id"],
            "adjusted": r["field"] == "PX_LAST",
            "fx": f"S{r['fx_series_id']}" if r["fx_series_id"] is not None else None,
        }
    return weights, legs


def select_series(conn, meta: dict) -> dict:
    """
    {key: {series_id, adjusted, fx}} for the held securities that have
    a usable price series; the rule is in the header. An entity absent
    from the result has no price history at all.
    """
    if not meta:
        return {}
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sr.entity_id, sr.series_id, sr.field, sr.source, sr.status,
               UPPER(ds.currency) AS currency
        FROM series_registry sr
        LEFT JOIN dim_security ds ON ds.entity_id = sr.entity_id
        WHERE sr.entity_id = ANY(%s)
          AND (   (sr.field = 'PX_LAST' AND sr.source IN ('bloomberg', 'sbs'))
               OR (sr.field = 'PX_CLEAN_PCT' AND sr.source = 'sbs'))
        """, ([m["entity_id"] for m in meta.values()],))

    def rank(row: dict, is_bond: bool) -> int:
        if row["field"] == "PX_CLEAN_PCT":
            return 0 if is_bond else 9
        if row["source"] == "bloomberg":
            return 1 if row["status"] == "active" else 8
        return 2

    best: dict[str, tuple[int, dict]] = {}
    for row in cur.fetchall():
        key = str(row["entity_id"])
        score = rank(row, meta[key]["ind_clase"] == IND_CLASE_BOND)
        if score < 8 and (key not in best or score < best[key][0]):
            best[key] = (score, row)

    out = {}
    for key, (_, row) in best.items():
        currency = meta[key]["currency"]
        if row["source"] == "bloomberg" and row["currency"]:
            currency = row["currency"]
        out[key] = {
            "series_id": row["series_id"],
            "adjusted": row["field"] == "PX_LAST",
            "fx": None if currency == BASE else ccy_key(currency),
        }
    return out


def prices(conn, adjusted_ids: list[int], raw_ids: list[int],
           start: date, end: date) -> pd.DataFrame:
    """
    date x series_id price frame. adjusted_ids come back-adjusted from
    vw_prices_adjusted (PX_LAST series only); raw_ids as stored.
    """
    cur = conn.cursor()
    rows = []
    for sql, ids in (
        ("SELECT series_id, date, price_adjusted AS price FROM vw_prices_adjusted "
         "WHERE series_id = ANY(%s) AND date BETWEEN %s AND %s", adjusted_ids),
        ("SELECT series_id, date, price FROM fact_prices "
         "WHERE series_id = ANY(%s) AND date BETWEEN %s AND %s", raw_ids),
    ):
        if ids:
            cur.execute(sql, (list(ids), start, end))
            rows += cur.fetchall()
    if not rows:
        return pd.DataFrame()
    return (pd.DataFrame(rows)
            .pivot(index="date", columns="series_id", values="price").astype(float))


def fx_mid(conn, start: date, end: date) -> tuple[pd.DataFrame, dict]:
    """
    (date x 'CCY_<code>' frame of SBS mid rates in PEN, {code: procode
    used}). Mid = (PX_BID + PX_ASK) / 2 on days that carry both.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT e.procode, p.date, AVG(p.price) AS mid
        FROM fact_prices p
        JOIN series_registry sr ON sr.series_id = p.series_id
        JOIN dim_entity e       ON e.entity_id = sr.entity_id
        WHERE e.procode LIKE 'SBS_FX_%%'
          AND sr.source = 'sbs' AND sr.field IN ('PX_BID', 'PX_ASK')
          AND p.date BETWEEN %s AND %s
        GROUP BY e.procode, p.date
        HAVING COUNT(*) = 2
        """, (start, end))
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(), {}
    wide = (pd.DataFrame(rows)
            .pivot(index="date", columns="procode", values="mid").astype(float))

    # procode = SBS_FX_<nocional>_<contraparte>_<fuente>
    by_ccy: dict[str, list[str]] = {}
    for procode in wide.columns:
        by_ccy.setdefault(procode.split("_")[2].upper(), []).append(procode)
    chosen = {}
    for code, procodes in by_ccy.items():
        if FX_FUENTE.get(code) in procodes:
            chosen[code] = FX_FUENTE[code]
            continue
        vs_base = [p for p in procodes if p.split("_")[3].upper() == BASE]
        chosen[code] = max(vs_base or procodes, key=lambda p: wide[p].notna().sum())
    logger.info("FX sources: " + ", ".join(f"{c}={p}" for c, p in sorted(chosen.items())))
    frame = pd.DataFrame({ccy_key(c): wide[p] for c, p in chosen.items()})
    return frame, chosen


def names(conn, entity_ids: list[int]) -> dict:
    """{str(entity_id): procode} for labelling the output."""
    if not entity_ids:
        return {}
    cur = conn.cursor()
    cur.execute("SELECT entity_id, procode FROM dim_entity WHERE entity_id = ANY(%s)",
                (list(entity_ids),))
    return {str(r["entity_id"]): r["procode"] for r in cur.fetchall()}
