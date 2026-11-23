# web/api/services/benchmarks.py
# ---------------------------------------------------------------------------
# Benchmarks service: what the tablero reads and writes about the
# composite indices, over src/pipelines/analytics/benchmark. The analytics
# module owns the rules (append-only versions, the chaining arithmetic,
# the three outputs); this module shapes its answers for the screen and
# resolves the catalog of series a basket can draw from.
# ---------------------------------------------------------------------------
from __future__ import annotations

import io
import logging

import pandas as pd

from src.db import onboard
from src.db.connection import get_connection
from src.db.queries import get_entity_id
from src.pipelines.analytics.benchmark import definitions, removal
from src.pipelines.analytics.benchmark.materialize import SOURCE_MODEL, materialize
from src.pipelines.prices.sbs.valor_cuota import afps
from web.api.services.spp import read as read_book, recent_cut

logger = logging.getLogger(__name__)


def _fund_of(procode) -> int | None:
    """The fund type behind an assigned entity's procode (SPP_X_F2 -> 2)."""
    parsed = afps.parse_procode(procode or "")
    return parsed[1] if parsed else None


def _summary(conn, bench: dict) -> dict:
    """One benchmark with its coverage: versions, stored levels, last level,
    and the house fund it is assigned to."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM dim_benchmark_version WHERE portfolio_id = %s",
                (bench["portfolio_id"],))
    versions = cur.fetchone()["n"]
    points, first, last, last_level = 0, None, None, None
    if bench["series_id"] is not None:
        cur.execute(
            "SELECT COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last "
            "FROM fact_prices WHERE series_id = %s", (bench["series_id"],))
        row = cur.fetchone()
        points, first, last = row["n"], row["first"], row["last"]
        if last is not None:
            cur.execute("SELECT price FROM fact_prices WHERE series_id = %s AND date = %s",
                        (bench["series_id"], last))
            last_level = float(cur.fetchone()["price"])
    return {
        "portfolio_id": bench["portfolio_id"], "procode": bench["procode"],
        "display_name": bench["display_name"] or bench["procode"],
        "base_currency": bench["base_currency"], "status": bench["status"],
        "entity_id": bench["entity_id"], "series_id": bench["series_id"],
        "versions": int(versions), "points": int(points),
        "from": str(first) if first else None, "to": str(last) if last else None,
        "last_level": last_level,
        "fund": _fund_of(bench.get("assigned_procode")),
        "assigned_to": bench.get("assigned_procode"),
    }


def list_benchmarks() -> list[dict]:
    with get_connection() as conn:
        return [_summary(conn, b) for b in definitions.benchmarks(conn, status=None)]


def get(procode: str) -> dict | None:
    with get_connection() as conn:
        bench = definitions.benchmark_by_procode(conn, procode)
        if bench is None or bench["portfolio_type"] != definitions.PORTFOLIO_TYPE:
            return None
        return _summary(conn, bench)


def create(procode: str, display_name: str, base_currency: str = "PEN",
           fund: int | None = None) -> dict:
    """
    Creates a benchmark or renames it (the procode is the identity) and,
    when a fund type is given, assigns it to the house's fund of that
    type - the block of the return tables it will appear in.
    """
    with get_connection() as conn:
        bench = definitions.ensure_benchmark(conn, procode, display_name, base_currency)
        if fund is not None:
            if int(fund) not in afps.funds():
                raise ValueError(f"Unknown fund type {fund}.")
            target = afps.procode(afps.house_afp(), int(fund))
            entity_id = get_entity_id(conn, target, "fund")
            if entity_id is None:
                raise ValueError(
                    f"{target} is not registered yet: load the valor cuota book first.")
            definitions.assign(conn, bench["portfolio_id"], entity_id)
            bench = definitions.benchmark_by_procode(conn, bench["procode"])
        return _summary(conn, bench)


def delete(procode: str) -> dict:
    """
    Removes a benchmark with its versions, levels and fund link. One
    transaction: either everything goes or nothing does.
    """
    with get_connection() as conn:
        result = removal.delete_benchmark(conn, _portfolio_id(conn, procode))
        conn.commit()
        return result


def _portfolio_id(conn, procode: str) -> int:
    bench = definitions.benchmark_by_procode(conn, procode)
    if bench is None or bench["portfolio_type"] != definitions.PORTFOLIO_TYPE:
        raise LookupError(procode)
    return bench["portfolio_id"]


def versions(procode: str) -> list[dict]:
    """Every version with its basket, oldest first."""
    with get_connection() as conn:
        pid = _portfolio_id(conn, procode)
        out = []
        for v in definitions.versions(conn, pid):
            basket = definitions.constituents(conn, v["version_id"])
            out.append({
                **{k: v[k] for k in ("version_id", "effective_from", "method",
                                     "rebalance_frequency", "change_type", "reason",
                                     "support_doc", "recorded_by", "supersedes_version_id")},
                "created_at": v["created_at"].isoformat(timespec="seconds"),
                "constituents": [{"series_id": c["series_id"], "label": c["label"],
                                  "weight": float(c["weight"]),
                                  "fx_series_id": c["fx_series_id"],
                                  "procode": c["procode"], "field": c["field"],
                                  "source": c["source"]} for c in basket],
            })
        return out


def changelog(procode: str) -> list[dict]:
    with get_connection() as conn:
        pid = _portfolio_id(conn, procode)
        rows = definitions.changelog(conn, pid)
    return [{**r, "created_at": r["created_at"].isoformat(timespec="seconds"),
             "weight_before": None if r["weight_before"] is None else float(r["weight_before"]),
             "weight_after": None if r["weight_after"] is None else float(r["weight_after"]),
             "weight_delta": float(r["weight_delta"])} for r in rows]


def add_version(procode: str, payload: dict) -> int:
    """Appends one version. ValueError from the writer names the problem."""
    with get_connection() as conn:
        pid = _portfolio_id(conn, procode)
        return definitions.add_version(
            conn, pid,
            effective_from=payload["effective_from"],
            method=payload.get("method") or "buy_and_hold",
            change_type=payload["change_type"],
            reason=payload.get("reason"),
            constituents=payload.get("constituents") or [],
            rebalance_frequency=payload.get("rebalance_frequency"),
            support_doc=payload.get("support_doc"),
            recorded_by=payload.get("recorded_by"),
            supersedes_version_id=payload.get("supersedes_version_id"),
        )


def rebuild(procode: str) -> dict:
    """Materializes one benchmark in its own transaction (runs in the task)."""
    with get_connection() as conn:
        pid = _portfolio_id(conn, procode)
        summary = materialize(conn, pid)
    summary["from"] = str(summary["from"])
    summary["to"] = str(summary["to"])
    return summary


# ---- The series a basket can draw from -------------------------------------
# The pipeline spine (series_registry over fact_prices), whatever the
# source - Bloomberg, the SBS vector, the SPP valor cuota - minus the model
# series themselves: an index built from another index is a loop waiting to
# happen. A series is found by procode, name, ticker, Bloomberg parsekyable
# or ISIN, the way the operator knows it. The coverage (first, last, count)
# is read for the rows returned, not for the whole registry, so a search
# stays cheap on a large book.

_CATALOG_SQL = """
    SELECT sr.series_id, sr.field, sr.source, sr.status, sr.frequency,
           sr.default_start_date,
           e.entity_id, e.procode, e.entity_type, e.name,
           ds.ticker, ds.currency, ds.security_type,
           bbg.id_value AS parsekyable, isin.id_value AS isin
    FROM series_registry sr
    JOIN dim_entity e ON e.entity_id = sr.entity_id
    LEFT JOIN dim_security ds ON ds.entity_id = e.entity_id
    LEFT JOIN vw_entity_identifier_current bbg
        ON bbg.entity_id = e.entity_id AND bbg.id_type = 'parsekyable'
       AND bbg.source = 'bloomberg'
    LEFT JOIN LATERAL (
        SELECT i.id_value FROM vw_entity_identifier_current i
        WHERE i.entity_id = e.entity_id AND i.id_type = 'isin'
        ORDER BY i.source LIMIT 1
    ) isin ON TRUE
    WHERE sr.domain = 'prices' AND sr.source <> %(model)s
"""


def _catalog_rows(conn, q: str = "", limit: int = 50,
                  series_id: int | None = None) -> list[dict]:
    """The catalog query on an open connection (the tests use it directly)."""
    sql, params = _CATALOG_SQL, {"model": SOURCE_MODEL, "limit": int(limit)}
    if series_id is not None:
        sql += " AND sr.series_id = %(series_id)s"
        params["series_id"] = int(series_id)
    if q.strip():
        sql += """
      AND (e.procode ILIKE %(q)s OR COALESCE(e.name, '') ILIKE %(q)s
           OR COALESCE(ds.ticker, '') ILIKE %(q)s
           OR COALESCE(ds.security_name, '') ILIKE %(q)s
           OR COALESCE(bbg.id_value, '') ILIKE %(q)s
           OR COALESCE(isin.id_value, '') ILIKE %(q)s)"""
        params["q"] = f"%{q.strip()}%"
    sql += " ORDER BY e.procode, sr.field, sr.source LIMIT %(limit)s"
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()

    coverage: dict[int, dict] = {}
    if rows:
        cur.execute(
            "SELECT series_id, COUNT(*) AS points, MIN(date) AS first, MAX(date) AS last "
            "FROM fact_prices WHERE series_id = ANY(%s) GROUP BY series_id",
            ([r["series_id"] for r in rows],))
        coverage = {c["series_id"]: c for c in cur.fetchall()}

    out = []
    for r in rows:
        cov = coverage.get(r["series_id"])
        ticker = r["parsekyable"] or r["ticker"]
        out.append({
            "series_id": r["series_id"], "entity_id": r["entity_id"],
            "procode": r["procode"], "entity_type": r["entity_type"], "name": r["name"],
            "field": r["field"], "source": r["source"], "status": r["status"],
            "frequency": r["frequency"],
            "default_start_date": (str(r["default_start_date"])
                                   if r["default_start_date"] else None),
            "ticker": ticker, "isin": r["isin"], "currency": r["currency"],
            "security_type": r["security_type"],
            "points": int(cov["points"]) if cov else 0,
            "from": str(cov["first"]) if cov else None,
            "to": str(cov["last"]) if cov else None,
            "label": r["procode"],
            "detail": " · ".join(x for x in (r["field"], r["source"], ticker or r["name"]) if x),
        })
    return out


def series_catalog(q: str = "", limit: int = 50) -> list[dict]:
    """Pickable series for the basket editor."""
    with get_connection() as conn:
        return _catalog_rows(conn, q, limit)


def register_series(payload: dict) -> dict:
    """
    Registers a Bloomberg ticker as a priced series (src.db.onboard does
    the writing) and answers with its catalog row plus `created`. The
    series has no prices yet: the Bloomberg machine fetches them on its
    next backfill run.
    """
    with get_connection() as conn:
        result = onboard.register_series(conn, payload)
        row = _catalog_rows(conn, limit=1, series_id=result["series_id"])[0]
    return {**row, "created": result["created"]}


def levels(procode: str, date_from=None, date_to=None) -> pd.DataFrame:
    """The stored level series as a DataFrame (date, level), ascending."""
    with get_connection() as conn:
        bench = definitions.benchmark_by_procode(conn, procode)
        if bench is None or bench["series_id"] is None:
            return pd.DataFrame(columns=["date", "level"])
        sql = "SELECT date, price AS level FROM fact_prices WHERE series_id = %s"
        params: list = [bench["series_id"]]
        if date_from:
            sql += " AND date >= %s::date"
            params.append(str(date_from))
        if date_to:
            sql += " AND date <= %s::date"
            params.append(str(date_to))
        cur = conn.cursor()
        cur.execute(sql + " ORDER BY date", params)
        rows = cur.fetchall()
    return pd.DataFrame([{"date": r["date"], "level": float(r["level"])} for r in rows],
                        columns=["date", "level"])


def export_xlsx(procode: str, date_from=None, date_to=None) -> bytes:
    """The level series as an in-memory Excel, most recent first."""
    df = levels(procode, date_from, date_to)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.sort_values("date", ascending=False).to_excel(
            writer, sheet_name=procode[:31], index=False)
    return buf.getvalue()


def levels_points(procode: str, date_from=None, date_to=None) -> dict | None:
    """The level series as [date, level] points, for the chart; None when
    the benchmark does not exist."""
    with get_connection() as conn:
        bench = definitions.benchmark_by_procode(conn, procode)
    if bench is None or bench["portfolio_type"] != definitions.PORTFOLIO_TYPE:
        return None
    df = levels(procode, date_from, date_to)
    return {"procode": procode, "display_name": bench["display_name"] or procode,
            "points": [[str(d), float(v)] for d, v in zip(df["date"], df["level"])]}


def benchmark_table(procode: str, limit: int | None) -> dict | None:
    """
    One benchmark's levels over the SAME date grid as the valor cuota book.

    The question this table answers is "which days is the index
    missing", so the rows are the days WITH valor cuota - not the days
    the index has. Listing only the latter would make a day without a
    level simply disappear, which is exactly what one needs to see.
    Returns None for an unknown benchmark.
    """
    with get_connection() as conn:
        bench = definitions.benchmark_by_procode(conn, procode)
        if bench is None or bench["series_id"] is None:
            return None
        date_from = recent_cut(["valor_cuota"], limit) if limit else None
        book = read_book(["valor_cuota"], date_from=date_from)
        if book.empty:
            return {"procode": procode, "display_name": bench["display_name"] or procode,
                    "columns": [], "rows": [], "missing": 0, "dates": 0}
        grid = (book if limit is None else book.tail(int(limit)))["date"]
        cur = conn.cursor()
        cur.execute(
            "SELECT date, price FROM fact_prices WHERE series_id = %s "
            "AND date BETWEEN %s AND %s",
            (bench["series_id"], grid.min(), grid.max()))
        stored = {r["date"]: float(r["price"]) for r in cur.fetchall()}

    rows, missing = [], 0
    for day in grid.iloc[::-1]:
        level = stored.get(day)
        if level is None:
            missing += 1
        rows.append({"date": str(day), "values": [level]})
    return {"procode": procode, "display_name": bench["display_name"] or procode,
            "columns": [{"col": procode, "label": bench["display_name"] or procode}],
            "rows": rows, "missing": missing, "dates": len(rows)}
