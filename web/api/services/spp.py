# web/api/services/spp.py
# ---------------------------------------------------------------------------
# SPP valor cuota service: everything the tablero reads and nothing that
# writes. Ported from the standalone monitor's read paths (status / windows
# / positions / table) onto the dimensional model.
#
# The monitor stored the book as one wide table; here it lives long in
# fact_prices. read() rebuilds the wide frame (date x '{key}_f{n}[suffix]')
# for the views that show the book itself (table, export). The analytical
# views do NOT pivot the book: windows() and positions() read the closing
# grid (the distinct dates) and then the handful of closes they compare,
# series() reads the lines it draws, status() aggregates in SQL. The book
# is 78k rows and growing; pivoting it on every request cost over a
# second per view, almost all of it in Python, while the SQL takes tens
# of milliseconds. The calculations stay share-grid by nature: one common
# closing-date grid for all AFPs, or relative performance would compare
# different periods.
#
# Metric keys (valor_cuota, cuotas, fondo) are the SBS's own names for the
# published figures and stay as they come; everything else is English.
# ---------------------------------------------------------------------------
from __future__ import annotations

import calendar
import datetime as dt
import io
import logging

import numpy as np
import pandas as pd
from psycopg.rows import tuple_row

from src.db.connection import get_connection
from src.pipelines.analytics.benchmark import definitions as bench_defs
from src.pipelines.prices.sbs.valor_cuota import afps

logger = logging.getLogger(__name__)

SUFFIX = {"valor_cuota": "", "cuotas": "_cuotas", "fondo": "_fondo"}
METRICS = list(SUFFIX)

# What the tablero's selector OFFERS, which is not what is stored. 'cuotas'
# is still scraped, loaded and served - the closing table shows it and the
# API accepts it by name - but as a series to chart it says nothing: units
# outstanding are administrative, not a price, and their return means
# nothing. Dropping it from METRICS would have dropped it from the data:
# that list also decides which columns are read.
PANEL_METRICS = ["valor_cuota", "fondo"]

SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Window keys and labels. Column order is NEWEST FIRST: the control date on
# the left, the longest window on the right - the desk reads "what happened
# today" first and works back. Mixed windows are ordered by their START
# date, most recent start first: the daily moves < 5D < MTD < M-1 < M-2 <
# 3M < 6M < YTD < FY < 12M < last year < the trailing years. (YTD and 6M
# swap places in the second half of the year; the column does not move
# with the calendar - reordering it twice a year would cost more than it
# clarifies. Same for FY, which overtakes the calendar year only in
# November and December.) The monthly positions keep the chronological
# order, oldest month on the left and the MTD last, like a time axis.
LEVEL_KEYS = ["t", "t1", "t2", "t3", "t4", "t5",
              "month", "month1", "month2", "year", "year1"]
# (key, fixed label or None for a date label, unit). Up to 6M the figures
# are basis points; from YTD on, percent. The trailing windows count back
# from the control date: 3M / 6M / 12M are plain returns, 3Y / 5Y / 10Y
# are ANNUALIZED (compound rate per year over the window).
RETURN_KEYS = [
    ("d0",    None,          "bps"),
    ("d1",    None,          "bps"),
    ("d2",    None,          "bps"),
    ("d3",    None,          "bps"),
    ("d5",    "5D",          "bps"),
    ("mtd",   "MTD",         "bps"),
    ("m1",    None,          "bps"),
    ("m2",    None,          "bps"),
    ("m3",    "3M",          "bps"),
    ("m6",    "6M",          "bps"),
    ("ytd",   "YTD",         "pct"),
    ("fy",    "FY",          "pct"),
    ("m12",   "12M",         "pct"),
    ("a1",    None,          "pct"),
    ("a3",    "3Y",          "pct"),
    ("a5",    "5Y",          "pct"),
    ("a10",   "10Y",         "pct"),
    ("level", "Unit value",  "level"),
]
# Years behind each annualized window.
ANNUALIZED_YEARS = {"a3": 3, "a5": 5, "a10": 10}


def column(afp, fund: int, metric: str = "valor_cuota") -> str:
    """'{key}_f{n}[suffix]' - the monitor's stable column identity."""
    key = afps.key_of(afp)
    if not key:
        raise ValueError(f"Unknown AFP: {afp}")
    return f"{key}_f{int(fund)}{SUFFIX[metric]}"


def house() -> str:
    """The house AFP's display name."""
    return afps.name_of(afps.house_afp())


def _valid_metric(metric) -> str:
    m = str(metric or "valor_cuota").strip()
    return m if m in SUFFIX else "valor_cuota"


def _requested_metrics(metric: str) -> list[str]:
    """'all' -> the three metrics, anything else -> its valid one."""
    return list(METRICS) if metric == "all" else [_valid_metric(metric)]


def _control_date(dates: list, day) -> dt.date | None:
    """Snaps the requested control date to the last close <= it (the
    common rule of windows() and positions()); None when nothing
    precedes it."""
    control = pd.to_datetime(day).date() if day else dates[-1]
    if control in dates:
        return control
    earlier = [d for d in dates if d <= control]
    return earlier[-1] if earlier else None


def _cell(by_date: pd.DataFrame, col: str, day):
    """One cell of the wide book, None-safe (kept for callers that still
    hold a pivoted frame)."""
    if day is None or col not in by_date.columns:
        return None
    try:
        v = by_date.at[day, col]
    except KeyError:
        return None
    return None if pd.isna(v) else float(v)


# ---- The series behind the book -------------------------------------------

def _registry(metrics: list[str], funds=None, names=None) -> list[dict]:
    """
    The SPP series the requested metrics live in, with the wide-book column
    each one becomes: one row per AFP x fund x metric, resolved once per
    request from series_registry. Optionally narrowed to some funds and
    AFP display names.
    """
    fields = [afps.METRIC_FIELD[m] for m in metrics]
    name_of = {a["key"]: a["name"] for a in afps.afps()}
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sr.series_id, e.procode, sr.field
            FROM series_registry sr
            JOIN dim_entity e ON e.entity_id = sr.entity_id
            WHERE e.procode LIKE 'SPP\\_%%' AND e.entity_type = 'fund'
              AND sr.source = %s AND sr.field = ANY(%s)
            ORDER BY sr.series_id
            """, (afps.SOURCE_SBS_SPP, fields))
        rows = cur.fetchall()
    out = []
    for r in rows:
        parsed = afps.parse_procode(r["procode"])
        metric = afps.FIELD_METRIC.get(r["field"])
        if parsed is None or metric is None:
            continue
        key, fund = parsed
        if funds is not None and fund not in funds:
            continue
        name = name_of.get(key, key)
        if names is not None and name not in names:
            continue
        out.append({"series_id": r["series_id"], "col": f"{key}_f{fund}{SUFFIX[metric]}",
                    "afp": name, "fund": fund, "metric": metric})
    return out


def _prices(series: list[dict], date_from=None, date_to=None,
            dates=None) -> pd.DataFrame:
    """Long frame (series_id, date, price) of the given series, bounded
    by a date range and/or restricted to an explicit list of dates."""
    if not series:
        return pd.DataFrame(columns=["series_id", "date", "price"])
    sql = "SELECT series_id, date, price FROM fact_prices WHERE series_id = ANY(%s)"
    params: list = [[s["series_id"] for s in series]]
    if date_from:
        sql += " AND date >= %s::date"
        params.append(str(date_from))
    if date_to:
        sql += " AND date <= %s::date"
        params.append(str(date_to))
    if dates is not None:
        sql += " AND date = ANY(%s)"
        params.append(list(dates))
    with get_connection() as conn:
        cur = conn.cursor(row_factory=tuple_row)
        cur.execute(sql, params)
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["series_id", "date", "price"])


def _grid(series: list[dict]) -> list[dt.date]:
    """The closing grid: every date any of the series quotes, ascending."""
    if not series:
        return []
    with get_connection() as conn:
        cur = conn.cursor(row_factory=tuple_row)
        cur.execute("SELECT DISTINCT date FROM fact_prices WHERE series_id = ANY(%s) ORDER BY date",
                    ([s["series_id"] for s in series],))
        return [r[0] for r in cur.fetchall()]


def _at(series: list[dict], dates) -> dict:
    """{column: {date: price}} for the given dates only."""
    wanted = sorted({d for d in dates if d is not None})
    out: dict = {s["col"]: {} for s in series}
    if not wanted:
        return out
    col_of = {s["series_id"]: s["col"] for s in series}
    for sid, day, price in _prices(series, dates=wanted).itertuples(index=False):
        out[col_of[sid]][day] = float(price)
    return out


def _last_date():
    """Cheap MAX(date) probe over the SPP series."""
    series = _registry(METRICS)
    if not series:
        return None
    with get_connection() as conn:
        cur = conn.cursor(row_factory=tuple_row)
        cur.execute("SELECT MAX(date) FROM fact_prices WHERE series_id = ANY(%s)",
                    ([s["series_id"] for s in series],))
        return cur.fetchone()[0]


# ---- Book reader ----------------------------------------------------------

def read(metrics: list[str] | None = None,
         date_from=None, date_to=None) -> pd.DataFrame:
    """
    Wide DataFrame: one row per date, one column per AFP x fund x metric,
    named '{key}_f{n}[suffix]'. Only the requested metrics are pivoted -
    the book views need one, an export may need all three.
    """
    metrics = [m for m in (metrics or METRICS) if m in SUFFIX] or METRICS
    series = _registry(metrics)
    long = _prices(series, date_from, date_to)
    if long.empty:
        return pd.DataFrame(columns=["date"])
    col_of = {s["series_id"]: s["col"] for s in series}
    long["col"] = long["series_id"].map(col_of)
    # pivot, not pivot_table: (series_id, date) is the primary key, so there
    # is nothing to aggregate, and the aggregating path is what cost a
    # second on the full book.
    df = long.pivot(index="date", columns="col", values="price").sort_index().reset_index()
    df.columns.name = None
    return df


# ---- Config ---------------------------------------------------------------

def config() -> dict:
    """The AFP registry the interface consumes - it never names an AFP."""
    return {
        "funds": afps.funds(),
        "house": house(),
        "metrics": [{"key": k, "label": afps.METRIC_LABEL[k]} for k in PANEL_METRICS],
        "afps": [{"key": a["key"],
                  "name": a["name"],
                  "color": a.get("color", "#1A1A1A"),
                  "color_solid": a.get("color_solid", a.get("color", "#1A1A1A")),
                  "house": bool(a.get("house")),
                  "funds": afps.funds_of(a["key"])}
                 for a in afps.afps()],
    }


# ---- Status ---------------------------------------------------------------

def gaps(dates, minimum: int = 3) -> list:
    """
    Consecutive business days without data inside the loaded range. The
    source has no holiday calendar, so Peruvian holidays show as empty
    business days; typical blocks span 2, so only runs of `minimum`+ are
    real missing information.
    """
    dates = list(dates)
    if len(dates) < 2:
        return []
    # Dates arrive as date objects from the database; the conversion is
    # only for strings and timestamps (pd.to_datetime on 8k dates alone
    # cost a quarter of a second).
    loaded = {(d.date() if isinstance(d, dt.datetime) else d) if isinstance(d, dt.date)
              else pd.to_datetime(d).date() for d in dates}
    missing = sorted(set(pd.bdate_range(min(loaded), max(loaded)).date) - loaded)
    if not missing:
        return []
    runs, current = [], [missing[0]]
    for previous, following in zip(missing, missing[1:]):
        if int(np.busday_count(previous + dt.timedelta(days=1), following)) == 0:
            current.append(following)
        else:
            runs.append(current)
            current = [following]
    runs.append(current)
    return [{"from": str(r[0]), "to": str(r[-1]), "days": len(r)}
            for r in runs if len(r) >= minimum]


def status() -> dict:
    """
    Summary for the tablero: coverage, latest values, gaps, variations.
    Aggregated in SQL - the only Python pass is over the distinct dates.
    """
    series = _registry(METRICS)
    base = {"table": "fact_prices", "rows": 0, "from": None, "to": None,
            "gaps": [], "series": [], "complete": 0, "partial": 0,
            "metrics": [{"metric": m, "dates": 0, "from": None, "to": None} for m in METRICS]}
    if not series:
        return base
    ids = [s["series_id"] for s in series]
    by_metric = {m: [s["series_id"] for s in series if s["metric"] == m] for m in METRICS}

    with get_connection() as conn:
        cur = conn.cursor(row_factory=tuple_row)
        cur.execute("SELECT DISTINCT date FROM fact_prices WHERE series_id = ANY(%s) ORDER BY date",
                    (ids,))
        all_dates = [r[0] for r in cur.fetchall()]
        if not all_dates:
            return base

        coverage = []
        for m in METRICS:
            cur.execute(
                "SELECT COUNT(DISTINCT date), MIN(date), MAX(date) "
                "FROM fact_prices WHERE series_id = ANY(%s)", (by_metric[m],))
            n, first, last = cur.fetchone()
            coverage.append({"metric": m, "dates": int(n),
                             "from": str(first) if first else None,
                             "to": str(last) if last else None})

        cur.execute(
            "SELECT series_id, COUNT(*), MIN(date) FROM fact_prices "
            "WHERE series_id = ANY(%s) GROUP BY series_id", (ids,))
        agg = {sid: (int(n), first) for sid, n, first in cur.fetchall()}

        # The last two closes of every series, for the level and its move.
        cur.execute(
            """
            SELECT series_id, date, price FROM (
                SELECT series_id, date, price,
                       ROW_NUMBER() OVER (PARTITION BY series_id ORDER BY date DESC) AS rn
                FROM fact_prices WHERE series_id = ANY(%s)) t
            WHERE rn <= 2
            ORDER BY series_id, date DESC
            """, (ids,))
        last_two: dict[int, list] = {}
        for sid, day, price in cur.fetchall():
            last_two.setdefault(sid, []).append((day, float(price)))

        # A "complete" date has all valor cuota series published; usable
        # coverage runs to the last of those - a half-published date is not
        # coverage. The series counted are the valor cuota series that have
        # any data at all, as the pivoted book would show them.
        vc_ids = [sid for sid in by_metric["valor_cuota"] if sid in agg]
        complete_dates: list = []
        if vc_ids:
            cur.execute(
                "SELECT date, COUNT(*) FROM fact_prices WHERE series_id = ANY(%s) "
                "GROUP BY date HAVING COUNT(*) = %s ORDER BY date", (vc_ids, len(vc_ids)))
            complete_dates = [r[0] for r in cur.fetchall()]

    base.update({"rows": len(all_dates), "from": str(all_dates[0]), "to": str(all_dates[-1]),
                 "gaps": gaps(all_dates), "metrics": coverage})
    base["complete"] = len(complete_dates)
    base["partial"] = len(all_dates) - len(complete_dates)
    base["from_complete"] = str(complete_dates[0]) if complete_dates else None
    base["to_complete"] = str(complete_dates[-1]) if complete_dates else None
    base["partial_after"] = (
        sum(1 for d in all_dates if d > complete_dates[-1]) if complete_dates else 0)

    # Staleness KPI, owned here next to the coverage semantics it depends
    # on (the dashboard only renders tone): business days elapsed since
    # the last COMPLETE close, and the data-quality classification.
    end = complete_dates[-1] if complete_dates else all_dates[-1]
    today = dt.date.today()
    lag = int(np.busday_count(end + dt.timedelta(days=1),
                              today + dt.timedelta(days=1))) if end < today else 0
    open_gaps = len(base["gaps"])
    if lag > 3 or open_gaps > 1:
        integrity = "exceeded"
    elif open_gaps == 1 or lag > 1:
        integrity = "watch"
    else:
        integrity = "ok"
    base["lag_business_days"] = max(lag, 0)
    base["integrity"] = integrity

    by_col = {s["col"]: s for s in series}

    def last_and_change(col):
        s = by_col.get(col)
        closes = last_two.get(s["series_id"]) if s else None
        if not closes:
            return None, None, None
        (last_day, last), *rest = closes
        previous = rest[0][1] if rest else None
        change = None if not previous else round((last / previous - 1) * 10000, 1)
        return last, change, str(last_day)

    for f in afps.funds():
        for a in afps.names():
            if not afps.operates(a, f):
                continue
            col = column(a, f)
            s = by_col.get(col)
            if s is None or s["series_id"] not in agg:
                continue
            points, start = agg[s["series_id"]]
            vc, vc_change, vc_date = last_and_change(col)
            units, units_change, units_date = last_and_change(column(a, f, "cuotas"))
            value, value_change, value_date = last_and_change(column(a, f, "fondo"))
            base["series"].append({
                "afp": a, "fund": f, "column": col,
                "date": vc_date, "value": vc, "var_bps": vc_change,
                "cuotas": units, "cuotas_var_bps": units_change, "cuotas_date": units_date,
                "fondo_soles": value, "fondo_var_bps": value_change, "fondo_date": value_date,
                "points": points,
                "start": str(start),
            })
    return base


def last_scheduled_run() -> dict | None:
    """
    The last unattended scrape as the scheduler recorded it in job_runs
    (acquire_spp and its retries), or None when none has run. The
    scrape leaves no trail of its own: job_runs is the record.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT job_id, machine_id, status, run_date, started_at, finished_at, error
            FROM job_runs
            WHERE job_id LIKE 'acquire_spp%%'
            ORDER BY started_at DESC
            LIMIT 1
            """)
        row = cur.fetchone()
    if row is None:
        return None
    return {"job_id": row["job_id"], "machine_id": row["machine_id"],
            "status": row["status"],
            "run_date": str(row["run_date"]) if row["run_date"] else None,
            "started_at": row["started_at"].isoformat(timespec="seconds"),
            "finished_at": (row["finished_at"].isoformat(timespec="seconds")
                            if row["finished_at"] else None),
            "error": row["error"]}


# ---- Series (chart) -------------------------------------------------------

def series(fund: int, requested_afps: list[str] | None, metric: str,
           date_from=None, date_to=None) -> dict:
    """The lines of one fund: only the requested AFPs' series are read."""
    metric = _valid_metric(metric)
    fund = int(fund) if int(fund) in afps.funds() else afps.funds()[0]
    names = afps.names()
    requested = [a for a in (requested_afps or []) if a in names] or names

    reg = _registry([metric], funds=[fund], names=requested)
    long = _prices(reg, date_from, date_to)
    points: dict[int, list] = {}
    if not long.empty:
        long = long.sort_values("date")
        for sid, g in long.groupby("series_id", sort=False):
            points[sid] = [[str(d), float(v)] for d, v in zip(g["date"], g["price"])]

    by_afp = {s["afp"]: s["series_id"] for s in reg}
    return {"fund": fund, "metric": metric,
            "series": [{"afp": afp, "points": points.get(by_afp.get(afp), [])}
                       for afp in requested]}


# ---- Performance windows --------------------------------------------------

def _dm(d):
    return d.strftime("%d/%m") if d else "—"


def _dmy(d):
    return d.strftime("%d/%m/%y") if d else "—"


def _short_month(d):
    return f"{SHORT_MONTHS[d.month - 1]} {d.strftime('%y')}" if d else "—"


def _first_of_month(d: dt.date, back: int = 0) -> dt.date:
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12
        y -= 1
    return dt.date(y, m, 1)


def _months_back(d: dt.date, months: int) -> dt.date:
    """The same calendar day `months` earlier, clamped to that month's
    length (31/05 minus three months is 28/02 or 29/02)."""
    first = _first_of_month(d, months)
    return dt.date(first.year, first.month,
                   min(d.day, calendar.monthrange(first.year, first.month)[1]))


def fiscal_year_start(d: dt.date) -> dt.date:
    """
    Day the fiscal year containing `d` starts. The year runs 31/10 to
    31/10.

    The boundary date belongs to the year that ENDS on it: standing on
    31/10 the operator is reading the twelve months just closed, not a
    year that is one day old. So the new year starts on 1/11, and the
    close of 31/10 is its base - which is what "since 31/10" means.
    """
    year = d.year if d > dt.date(d.year, 10, 31) else d.year - 1
    return dt.date(year, 11, 1)


def _last_before(dates: list, limit: dt.date):
    earlier = [d for d in dates if d < limit]
    return earlier[-1] if earlier else None


def _last_on_or_before(dates: list, limit: dt.date):
    earlier = [d for d in dates if d <= limit]
    return earlier[-1] if earlier else None


def reference_dates(dates: list, control: dt.date) -> dict:
    """
    Base dates of every window on the COMMON closing grid - one grid for
    all series, or relative returns would compare different periods.
    Month/year bases are the LAST CLOSE OF THE PRIOR PERIOD (the desk
    standard), so MTD includes the first business day. The trailing
    windows (3M ... 10A) are based on the close ON OR BEFORE the same
    calendar day that many months or years back, so a 12M window spans
    exactly one year when that day traded.
    """
    earlier = [d for d in dates if d < control]
    ref = {"t": control}
    for i in range(1, 6):
        ref[f"t{i}"] = earlier[-i] if len(earlier) >= i else None
    ref["month"] = _last_before(dates, _first_of_month(control, 0))
    ref["month1"] = _last_before(dates, _first_of_month(control, 1))
    ref["month2"] = _last_before(dates, _first_of_month(control, 2))
    ref["year"] = _last_before(dates, dt.date(control.year, 1, 1))
    ref["year1"] = _last_before(dates, dt.date(control.year - 1, 1, 1))
    # The fiscal year: same rule as the month and the year - the base is
    # the last close of the previous period, i.e. 31/10 (or October's last
    # trading day when the 31st fell on a weekend or holiday).
    ref["fy"] = _last_before(dates, fiscal_year_start(control))
    for key, months in (("m3", 3), ("m6", 6), ("m12", 12),
                        ("a3", 36), ("a5", 60), ("a10", 120)):
        ref[key] = _last_on_or_before(dates, _months_back(control, months))
    return ref


def _change(current, base):
    if current is None or base is None or base == 0:
        return None
    return current / base - 1.0


def _annualized(current, base, years: float):
    """Compound rate per year over the window; None without both ends."""
    if current is None or base is None or base <= 0 or current <= 0:
        return None
    return (current / base) ** (1.0 / years) - 1.0


def _window_returns(v: dict) -> dict:
    """The return columns of one row from its levels at the reference
    dates (`v`: window key -> level, None when unavailable)."""
    g = v.get
    r = {
        "level": g("t"),
        "d0":  _change(g("t"),  g("t1")),
        "d1":  _change(g("t1"), g("t2")),
        "d2":  _change(g("t2"), g("t3")),
        "d3":  _change(g("t3"), g("t4")),
        "d5":  _change(g("t"),  g("t5")),
        "mtd": _change(g("t"),  g("month")),
        "m1":  _change(g("month"),  g("month1")),
        "m2":  _change(g("month1"), g("month2")),
        "m3":  _change(g("t"),    g("m3")),
        "m6":  _change(g("t"),    g("m6")),
        "fy":  _change(g("t"),    g("fy")),
        "ytd": _change(g("t"),    g("year")),
        "m12": _change(g("t"),    g("m12")),
        "a1":  _change(g("year"), g("year1")),
    }
    for k, years in ANNUALIZED_YEARS.items():
        r[k] = _annualized(g("t"), g(k), years)
    return r


def _excess(base: dict, other: dict, keys: list) -> dict:
    """base minus other per window; None where either side is missing."""
    return {k: (None if (base.get(k) is None or other.get(k) is None)
                else base[k] - other[k]) for k in keys}


# ---- The benchmarks inside the tables -------------------------------------
# An index declared in the Benchmarks module and ASSIGNED to a house fund
# (benchmark_assignment, through the analytics module) is one more row of
# that fund's block: its own returns in the absolute table, the house's
# excess over it in the relative one. Only for the valor cuota metric -
# an index has no fund size.

def _assigned_benchmarks(conn) -> dict[int, list[dict]]:
    """{fund type: [benchmark rows]} for the active benchmarks assigned
    to a fund entity (the entity's procode says which type)."""
    out: dict[int, list[dict]] = {}
    for b in bench_defs.assignments(conn):
        parsed = afps.parse_procode(b["assigned_procode"])
        if parsed is None or b["series_id"] is None:
            continue
        out.setdefault(parsed[1], []).append(b)
    return out


def _levels_at_or_before(conn, series_ids: list[int], dates) -> dict[int, dict]:
    """
    {series_id: {date: level}} with the last level AT OR BEFORE each date.
    A benchmark quotes on its constituents' calendar, which need not be
    the SPP grid: a day the index did not print must not read as a
    missing index.
    """
    wanted = sorted({d for d in dates if d is not None})
    out: dict[int, dict] = {sid: {} for sid in series_ids}
    if not series_ids or not wanted:
        return out
    cur = conn.cursor(row_factory=tuple_row)
    cur.execute(
        """
        SELECT s.series_id, d.day, l.price
        FROM unnest(%s::int[]) AS s(series_id)
        CROSS JOIN unnest(%s::date[]) AS d(day)
        LEFT JOIN LATERAL (
            SELECT price FROM fact_prices f
            WHERE f.series_id = s.series_id AND f.date <= d.day
            ORDER BY f.date DESC LIMIT 1
        ) l ON TRUE
        """, (list(series_ids), wanted))
    for sid, day, price in cur.fetchall():
        if price is not None:
            out[sid][day] = float(price)
    return out


def _benchmark_rows(conn, ref: dict, house_returns: dict) -> tuple[list, list, list]:
    """
    The benchmark rows of the windows tables, for every fund type with
    assigned benchmarks: levels at the reference dates (forward-filled),
    the returns, and the house's excess over each index.
    `house_returns`: {fund: the house's return row} for the relative table.
    Returns (levels, absolute, relative), every row flagged benchmark=True.
    """
    by_fund = _assigned_benchmarks(conn)
    if not by_fund:
        return [], [], []
    series_ids = [b["series_id"] for bs in by_fund.values() for b in bs]
    by_series = _levels_at_or_before(conn, series_ids, ref.values())
    keys = [k for k, _, u in RETURN_KEYS if u != "level"]
    levels, absolute, relative = [], [], []
    for fund in sorted(by_fund):
        for b in by_fund[fund]:
            lv = by_series.get(b["series_id"], {})
            v = {k: (lv.get(d) if d is not None else None) for k, d in ref.items()}
            r = _window_returns(v)
            tag = {"afp": b["display_name"] or b["procode"], "fund": fund,
                   "benchmark": True, "procode": b["procode"]}
            levels.append({**tag, "values": {k: v.get(k) for k in LEVEL_KEYS}})
            absolute.append({**tag, "values": r})
            base = house_returns.get(fund)
            if base is not None:
                relative.append({**tag, "absolute": False, "values": _excess(base, r, keys)})
    return levels, absolute, relative


def windows(day=None, metric: str = "valor_cuota") -> dict:
    """
    Levels, absolute returns and relative returns (house minus each
    competitor, and minus each benchmark assigned to the fund) per AFP
    and fund, on the common closing grid. Reads the grid and then only
    the closes the windows compare.
    """
    metric = _valid_metric(metric)
    empty = {"control": None, "dates": {}, "metric": metric, "levels": [],
             "absolute": [], "relative": [], "level_columns": [], "return_columns": []}
    reg = _registry([metric])
    dates = _grid(reg)
    if not dates:
        return empty
    control = _control_date(dates, day)
    if control is None:
        return empty

    ref = reference_dates(dates, control)
    values = _at(reg, ref.values())

    def cell(col, d):
        return values.get(col, {}).get(d) if d is not None else None

    names = afps.names()
    house_afp = house()
    levels, absolute, returns = [], [], {}

    for f in afps.funds():
        for a in names:
            if not afps.operates(a, f):
                continue
            col = column(a, f, metric)
            v = {k: cell(col, ref.get(k)) for k in ref}
            levels.append({"afp": a, "fund": f,
                           "values": {k: v.get(k) for k in LEVEL_KEYS}})
            r = _window_returns(v)
            returns[(a, f)] = r
            absolute.append({"afp": a, "fund": f, "values": r})

    # Relative: the house appears too, but with its ABSOLUTE return - the
    # baseline the differences are read against. For the annualized
    # windows the difference is between annualized rates.
    keys = [k for k, _, u in RETURN_KEYS if u != "level"]
    relative = []
    for f in afps.funds():
        if (house_afp, f) not in returns:
            continue
        base = returns[(house_afp, f)]
        relative.append({"afp": house_afp, "fund": f, "absolute": True,
                         "values": {k: base.get(k) for k in keys}})
        for a in names:
            if a == house_afp or (a, f) not in returns:
                continue
            relative.append({"afp": a, "fund": f, "absolute": False,
                             "values": _excess(base, returns[(a, f)], keys)})

    if metric == "valor_cuota":
        house_rows = {f: returns[(house_afp, f)] for f in afps.funds()
                      if (house_afp, f) in returns}
        with get_connection() as conn:
            b_levels, b_absolute, b_relative = _benchmark_rows(conn, ref, house_rows)
        levels += b_levels
        absolute += b_absolute
        relative += b_relative

    level_columns = [[k, (_dmy(ref[k]) if k in ("year", "year1") else _dm(ref[k]))]
                     for k in LEVEL_KEYS]
    return_labels = {
        "d0": _dm(ref["t"]), "d1": _dm(ref["t1"]),
        "d2": _dm(ref["t2"]), "d3": _dm(ref["t3"]),
        "m1": _short_month(ref["month"]),
        "m2": _short_month(ref["month1"]),
        "a1": ref["year"].strftime("%Y") if ref["year"] else "—",
    }
    return_columns = [[k, (fixed or return_labels.get(k, k)), u]
                      for k, fixed, u in RETURN_KEYS]

    return {"control": str(control),
            "dates": {k: (str(x) if x else None) for k, x in ref.items()},
            "metric": metric,
            "levels": levels, "absolute": absolute, "relative": relative,
            "level_columns": level_columns, "return_columns": return_columns}


# ---- Monthly positions ----------------------------------------------------

def positions(day=None, months: int = 24, metric: str = "valor_cuota") -> dict:
    """
    Monthly performance rank per AFP, grouped by FUND TYPE - ranking only
    makes sense between portfolios of the same risk profile. Each closed
    month returns close-vs-prior-close; the last period is the MTD. Reads
    the grid and then the month-end closes only.
    """
    metric = _valid_metric(metric)
    empty = {"control": None, "metric": metric, "total_afp": 0, "periods": [], "rows": []}
    reg = _registry([metric])
    dates = _grid(reg)
    if not dates:
        return empty
    control = _control_date(dates, day)
    if control is None:
        return empty

    by_month: dict[tuple, dt.date] = {}
    for d in dates:
        if d > control:
            break
        by_month[(d.year, d.month)] = d    # last close of each month wins
    closed = sorted(k for k in by_month if k < (control.year, control.month))
    if len(closed) < 2:
        return empty

    use = closed[-(int(months) + 1):]   # one extra as the first base
    periods = []
    for i in range(1, len(use)):
        year, month = use[i]
        periods.append({
            "key": f"{year:04d}-{month:02d}",
            "label": f"{SHORT_MONTHS[month - 1]} {str(year)[2:]}",
            "start": by_month[use[i - 1]], "end": by_month[use[i]],
        })
    periods.append({"key": "MTD", "label": "MTD",
                    "start": by_month[closed[-1]], "end": control})

    values = _at(reg, [p["start"] for p in periods] + [p["end"] for p in periods])

    def cell(col, d):
        return values.get(col, {}).get(d)

    names = afps.names()
    rows = []
    for f in afps.funds():
        competing = [a for a in names if afps.operates(a, f)]
        returns = {a: {} for a in competing}
        for p in periods:
            for a in competing:
                col = column(a, f, metric)
                returns[a][p["key"]] = _change(cell(col, p["end"]), cell(col, p["start"]))
        ranks = {a: {} for a in competing}
        for p in periods:
            k = p["key"]
            alive = [(a, returns[a][k]) for a in competing if returns[a][k] is not None]
            for rank, (a, _) in enumerate(sorted(alive, key=lambda x: -x[1]), start=1):
                ranks[a][k] = rank
        for a in competing:
            rows.append({"fund": f, "afp": a, "ranks": ranks[a], "returns": returns[a]})

    return {"control": str(control), "metric": metric,
            "total_afp": max((len([a for a in names if afps.operates(a, f)])
                              for f in afps.funds()), default=len(names)),
            "periods": [{"key": p["key"], "label": p["label"],
                         "from": str(p["start"]), "to": str(p["end"])}
                        for p in periods],
            "rows": rows}


# ---- Book (table + export) ------------------------------------------------

def requested_columns(metric: str, fund_arg: str) -> list[dict]:
    """
    Screen selection -> concrete columns, so the CSV downloads exactly
    what is on screen. Only combinations that exist: an AFP that does
    not operate a fund must not contribute a dash column.
    """
    metrics = _requested_metrics(metric)

    if fund_arg == "all":
        selected_funds = afps.funds()
    else:
        try:
            f = int(fund_arg)
            selected_funds = [f] if f in afps.funds() else afps.funds()
        except (TypeError, ValueError):
            selected_funds = afps.funds()

    return [{"col": column(a, f, m), "afp": a, "fund": f, "metric": m}
            for f in selected_funds for a in afps.names() if afps.operates(a, f)
            for m in metrics]


def recent_cut(metrics: list[str], n: int, date_to=None):
    """
    First date of the last `n` distinct dates carrying any of the given
    metrics - resolved in SQL so a 60-row view does not fetch and pivot
    the book since 1993 to keep 60 dates.
    """
    series = _registry(metrics)
    if not series:
        return None
    sql = "SELECT DISTINCT date FROM fact_prices WHERE series_id = ANY(%s)"
    params: list = [[s["series_id"] for s in series]]
    if date_to:
        sql += " AND date <= %s::date"
        params.append(str(date_to))
    sql += " ORDER BY date DESC LIMIT %s"
    params.append(int(n))
    with get_connection() as conn:
        cur = conn.cursor(row_factory=tuple_row)
        cur.execute(sql, params)
        rows = cur.fetchall()
    return rows[-1][0] if rows else None


def table(limit: int | None, metric: str, fund_arg: str) -> dict:
    """The book, most recent first. limit=None is the whole book."""
    selected = requested_columns(metric, fund_arg)
    metrics = _requested_metrics(metric)
    date_from = recent_cut(metrics, limit) if limit else None
    df = read(metrics, date_from=date_from)
    if df.empty:
        return {"columns": selected, "metric": metric, "fund": fund_arg, "rows": []}

    cut = (df if limit is None else df.tail(int(limit))).iloc[::-1]
    col_names = [c["col"] for c in selected]
    present = [c for c in col_names if c in df.columns]
    # One pass over the frame's values, not one .at lookup per cell.
    block = cut.reindex(columns=present)
    matrix = block.to_numpy(dtype=float)
    rows = []
    for day, values in zip(cut["date"], matrix):
        by_col = dict(zip(present, values))
        rows.append({"date": str(day),
                     "values": [None if (c not in by_col or np.isnan(by_col[c]))
                                else float(by_col[c]) for c in col_names]})
    return {"columns": selected, "metric": metric, "fund": fund_arg, "rows": rows}


def export_csv(metric: str, fund_arg: str, limit: int | None,
               date_from=None, date_to=None) -> bytes:
    """The same cut as table(), as CSV bytes built in memory."""
    selected = [c["col"] for c in requested_columns(metric, fund_arg)]
    metrics = _requested_metrics(metric)
    if limit:
        cut = recent_cut(metrics, limit, date_to=date_to)
        if cut is not None:
            date_from = max(pd.to_datetime(date_from).date(), cut) if date_from else cut
    df = read(metrics, date_from, date_to)
    present = [c for c in selected if c in df.columns]
    df = df[["date"] + present] if not df.empty else df
    if limit and not df.empty:
        df = df.tail(int(limit))
    if not df.empty:
        df = df.sort_values("date", ascending=False)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")
