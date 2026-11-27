# src/pipelines/analytics/benchmark/definitions.py
# ---------------------------------------------------------------
# The definition side of a benchmark (composite index): what it is,
# and what it is made of over time. Humans write definitions here;
# materialize.py turns them into facts. Nothing in this module reads
# or writes a fact table.
#
# A benchmark is portfolio-shaped (docs/PENDING_PIPELINES.md item 5):
#   dim_portfolio      procode, source 'model', portfolio_type
#                      'benchmark', display_name - the name business
#                      knows it by ("Renta mixta global 60/40")
#   dim_entity         entity_type 'index', same procode; bridged by
#                      dim_portfolio.parent_entity_id
#   series_registry    PX_LAST / prices / source 'model' on that
#                      entity: the level series materialize() writes
#   dim_benchmark_version + benchmark_constituent
#                      the append-only history of its composition
#
# APPEND-ONLY WRITER. add_version() inserts a version and its basket
# and that is the only kind of write this module makes to the two
# definition tables: there is no UPDATE and no DELETE here, and
# tests/analytics/benchmark/test_definitions.py checks that stays
# true. A change is a new version with its own effective_from, a
# change_type, a reason that cannot be blank, and the version it
# supersedes. The changelog is the view 44_vw_benchmark_changelog.sql.
#
# Which version governs a date: the one with the greatest
# effective_from at or before it; among versions sharing an
# effective_from, the highest version_id (a correction restating the
# same start date wins over what it corrects). governing_versions()
# is that rule, and materialize.build_periods() consumes it.
#
# Besides its definition a benchmark has an ASSIGNMENT: the entity it
# is the benchmark of (the house fund of one type), in
# benchmark_assignment (45). That is a link, not a definition - one row
# per benchmark, replaced when reassigned - and the tablero reads it to
# show the index inside that fund's return tables.
# ---------------------------------------------------------------

import datetime as dt
import logging

from src.db.queries import get_or_create_entity_id

logger = logging.getLogger(__name__)

SOURCE_MODEL = "model"
PORTFOLIO_TYPE = "benchmark"
LEVEL_FIELD = "PX_LAST"
LEVEL_DOMAIN = "prices"
METHODS = ("buy_and_hold", "fixed_weights", "heuristic")
FREQUENCIES = ("daily", "monthly", "quarterly", "annual")
CHANGE_TYPES = ("initial", "rebalance", "methodology", "constituent_change",
                "correction")


# ---- The benchmark itself ---------------------------------------------

def ensure_benchmark(conn, procode: str, display_name: str,
                     base_currency: str = "PEN") -> dict:
    """
    Creates the three rows a benchmark stands on (entity, level series,
    portfolio) or finds them. Idempotent. Re-running with a new
    display_name renames it: the name is display-only, the procode is
    the identity.

    Returns {'portfolio_id', 'entity_id', 'series_id', 'procode'}.
    """
    procode = str(procode or "").strip()
    display_name = str(display_name or "").strip()
    if not procode:
        raise ValueError("A benchmark needs a procode.")
    if not display_name:
        raise ValueError(f"Benchmark {procode} needs a display name.")

    entity_id = get_or_create_entity_id(conn, procode=procode,
                                        entity_type="index", name=display_name)
    cur = conn.cursor()
    cur.execute(
        "UPDATE dim_entity SET name = %s, updated_at = NOW() "
        "WHERE entity_id = %s AND name IS DISTINCT FROM %s",
        (display_name, entity_id, display_name))
    cur.execute(
        """
        INSERT INTO series_registry (entity_id, field, domain, source, frequency, status)
        VALUES (%s, %s, %s, %s, 'daily', 'active')
        ON CONFLICT (entity_id, field, source) DO NOTHING
        """,
        (entity_id, LEVEL_FIELD, LEVEL_DOMAIN, SOURCE_MODEL))
    cur.execute(
        """
        INSERT INTO dim_portfolio
            (procode, source, portfolio_type, display_name, base_currency,
             parent_entity_id, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'active')
        ON CONFLICT (procode, source) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            parent_entity_id = COALESCE(dim_portfolio.parent_entity_id,
                                        EXCLUDED.parent_entity_id),
            updated_at = NOW()
        WHERE dim_portfolio.display_name IS DISTINCT FROM EXCLUDED.display_name
           OR dim_portfolio.parent_entity_id IS NULL
        """,
        (procode, SOURCE_MODEL, PORTFOLIO_TYPE, display_name, base_currency,
         entity_id))
    bench = benchmark_by_procode(conn, procode)
    if bench["portfolio_type"] != PORTFOLIO_TYPE:
        raise ValueError(
            f"dim_portfolio ({procode}, {SOURCE_MODEL}) exists with "
            f"portfolio_type {bench['portfolio_type']!r}, not a benchmark.")
    logger.info(f"benchmark {procode}: portfolio_id={bench['portfolio_id']} "
                f"entity_id={bench['entity_id']} series_id={bench['series_id']}.")
    return bench


_BENCHMARK_SQL = """
    SELECT p.portfolio_id, p.procode, p.display_name, p.base_currency,
           p.portfolio_type, p.status, p.parent_entity_id AS entity_id,
           sr.series_id,
           ba.entity_id AS assigned_entity_id, ae.procode AS assigned_procode
    FROM dim_portfolio p
    LEFT JOIN series_registry sr
           ON sr.entity_id = p.parent_entity_id
          AND sr.field = %(field)s AND sr.source = %(source)s
    LEFT JOIN benchmark_assignment ba ON ba.portfolio_id = p.portfolio_id
    LEFT JOIN dim_entity ae ON ae.entity_id = ba.entity_id
    WHERE p.source = %(source)s
"""


def benchmark(conn, portfolio_id: int) -> dict | None:
    """One benchmark portfolio with its entity and level series, or None."""
    cur = conn.cursor()
    cur.execute(_BENCHMARK_SQL + " AND p.portfolio_id = %(id)s AND p.portfolio_type = %(type)s",
                {"field": LEVEL_FIELD, "source": SOURCE_MODEL, "id": portfolio_id,
                 "type": PORTFOLIO_TYPE})
    return cur.fetchone()


def benchmark_by_procode(conn, procode: str) -> dict | None:
    cur = conn.cursor()
    cur.execute(_BENCHMARK_SQL + " AND p.procode = %(procode)s",
                {"field": LEVEL_FIELD, "source": SOURCE_MODEL, "procode": procode})
    return cur.fetchone()


def benchmarks(conn, status: str | None = "active") -> list[dict]:
    """Every benchmark portfolio, optionally by status."""
    sql = _BENCHMARK_SQL + " AND p.portfolio_type = %(type)s"
    params = {"field": LEVEL_FIELD, "source": SOURCE_MODEL, "type": PORTFOLIO_TYPE}
    if status is not None:
        sql += " AND p.status = %(status)s"
        params["status"] = status
    cur = conn.cursor()
    cur.execute(sql + " ORDER BY p.procode", params)
    return cur.fetchall()


# ---- What a benchmark is the benchmark OF -------------------------------

def assign(conn, portfolio_id: int, entity_id: int) -> None:
    """
    Links a benchmark to the entity it measures - the house fund it is
    the benchmark of. One link per benchmark; assigning again replaces
    it. A link, not a definition: nothing in the versions or the levels
    changes, only where the tablero shows the index.
    """
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM dim_entity WHERE entity_id = %s", (entity_id,))
    if cur.fetchone() is None:
        raise ValueError(f"Entity {entity_id} does not exist.")
    cur.execute(
        """
        INSERT INTO benchmark_assignment (portfolio_id, entity_id)
        VALUES (%s, %s)
        ON CONFLICT (portfolio_id) DO UPDATE SET
            entity_id = EXCLUDED.entity_id, assigned_at = NOW()
        """, (portfolio_id, entity_id))
    logger.info(f"benchmark portfolio_id={portfolio_id} assigned to entity_id={entity_id}.")


def assignments(conn) -> list[dict]:
    """Every ACTIVE benchmark assigned to an entity, with that entity's
    procode and the benchmark's level series (benchmark_by_procode's
    shape)."""
    cur = conn.cursor()
    cur.execute(
        _BENCHMARK_SQL + """
          AND p.portfolio_type = %(type)s AND p.status = 'active'
          AND ba.entity_id IS NOT NULL
        ORDER BY p.procode
        """, {"field": LEVEL_FIELD, "source": SOURCE_MODEL, "type": PORTFOLIO_TYPE})
    return cur.fetchall()


# ---- Versions: validation (pure) -----------------------------------------

def validate_version(effective_from, method: str, change_type: str, reason,
                     constituents: list[dict],
                     rebalance_frequency: str | None = None,
                     today=None) -> dict:
    """
    Checks one version header and basket, with no database. Returns
    the cleaned header plus constituents with weights NORMALIZED to
    sum 1 (60/30/10 == 0.6/0.3/0.1 is accepted; anything that sums to
    neither 1 nor 100 is a typo, not a convention).

    constituents: [{'label', 'series_id', 'weight', 'fx_series_id'?}]

    Refuses, naming the problem: a blank reason, an unknown method or
    change_type, a rebalance frequency without fixed weights or fixed
    weights without one, a future effective date, an empty basket, a
    blank label, a non-positive weight, a repeated series.
    """
    reason = str(reason or "")
    if not reason.strip():
        raise ValueError("A version needs a reason; an empty one is refused.")
    reason = reason.strip()
    if method not in METHODS:
        raise ValueError(f"Unknown method {method!r}; one of {', '.join(METHODS)}.")
    if change_type not in CHANGE_TYPES:
        raise ValueError(
            f"Unknown change_type {change_type!r}; one of {', '.join(CHANGE_TYPES)}.")
    if method == "fixed_weights":
        if rebalance_frequency not in FREQUENCIES:
            raise ValueError(
                "fixed_weights needs a rebalance_frequency: "
                + ", ".join(FREQUENCIES) + ".")
    elif rebalance_frequency is not None:
        raise ValueError(
            f"rebalance_frequency only applies to fixed_weights, not {method}.")
    if isinstance(effective_from, str):
        effective_from = dt.date.fromisoformat(effective_from)
    if isinstance(effective_from, dt.datetime):
        effective_from = effective_from.date()
    if not isinstance(effective_from, dt.date):
        raise ValueError(f"effective_from must be a date, got {effective_from!r}.")
    if effective_from > (today or dt.date.today()):
        raise ValueError(f"effective_from {effective_from} is in the future.")
    if not constituents:
        raise ValueError("The basket cannot be empty.")

    cleaned, seen, total = [], set(), 0.0
    for c in constituents:
        label = str(c.get("label") or "").strip()
        if not label:
            raise ValueError("Every constituent needs a label (ticker).")
        try:
            series_id = int(c["series_id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"'{label}' needs a series_id.") from None
        try:
            weight = float(c.get("weight"))
        except (TypeError, ValueError):
            raise ValueError(f"'{label}' needs a numeric weight.") from None
        if weight <= 0:
            raise ValueError(f"The weight of '{label}' must be greater than zero.")
        if series_id in seen:
            raise ValueError(f"Series {series_id} ('{label}') repeats in the basket.")
        seen.add(series_id)
        fx = c.get("fx_series_id")
        fx_series_id = int(fx) if fx not in (None, "") else None
        cleaned.append({"label": label, "series_id": series_id, "weight": weight,
                        "fx_series_id": fx_series_id})
        total += weight

    if not (abs(total - 1.0) < 1e-6 or abs(total - 100.0) < 1e-4):
        raise ValueError(f"Weights sum to {total:g}; they must sum to 1 (or 100).")
    for c in cleaned:
        c["weight"] = c["weight"] / total

    return {"effective_from": effective_from, "method": method,
            "rebalance_frequency": rebalance_frequency if method == "fixed_weights" else None,
            "change_type": change_type, "reason": reason, "constituents": cleaned}


# ---- Versions: the append-only writer ------------------------------------

def add_version(conn, portfolio_id: int, *, effective_from, method: str,
                change_type: str, reason, constituents: list[dict],
                rebalance_frequency: str | None = None,
                support_doc: str | None = None, recorded_by: str | None = None,
                supersedes_version_id: int | None = None) -> int:
    """
    Appends one version of a benchmark's definition and its basket.
    Never updates or deletes an existing version: to change anything,
    add another version. Returns the new version_id.

    Beyond validate_version(): the portfolio must be a benchmark;
    'initial' is for the first version only and the first version
    must be 'initial'; every referenced series (price and FX legs)
    must exist and none may be the benchmark's own level series (an
    index that holds itself recomposes on every rebuild, drifting a
    little further each time with nothing to notice); the superseded
    version, when named, must belong to the same benchmark.
    """
    bench = benchmark(conn, portfolio_id)
    if bench is None:
        raise ValueError(f"portfolio_id={portfolio_id} is not a benchmark portfolio.")
    v = validate_version(effective_from, method, change_type, reason, constituents,
                         rebalance_frequency)

    existing = versions(conn, portfolio_id)
    if not existing and v["change_type"] != "initial":
        raise ValueError(
            f"{bench['procode']} has no versions yet: the first one is 'initial', "
            f"not {v['change_type']!r}.")
    if existing and v["change_type"] == "initial":
        raise ValueError(
            f"{bench['procode']} already has {len(existing)} version(s); "
            "'initial' is for the first one only.")
    if supersedes_version_id is not None:
        if not any(e["version_id"] == supersedes_version_id for e in existing):
            raise ValueError(
                f"supersedes_version_id={supersedes_version_id} is not a version "
                f"of {bench['procode']}.")

    cur = conn.cursor()
    own = bench["series_id"]
    for c in v["constituents"]:
        legs = [(c["series_id"], c["label"])]
        if c["fx_series_id"] is not None:
            legs.append((c["fx_series_id"], f"FX of {c['label']}"))
        for series_id, name in legs:
            if own is not None and series_id == own:
                raise ValueError(
                    f"{bench['procode']} cannot be a constituent of itself ('{name}').")
            cur.execute("SELECT 1 FROM series_registry WHERE series_id = %s", (series_id,))
            if cur.fetchone() is None:
                raise ValueError(f"The series of '{name}' (series_id {series_id}) does not exist.")

    cur.execute(
        """
        INSERT INTO dim_benchmark_version
            (portfolio_id, effective_from, method, rebalance_frequency, change_type,
             reason, support_doc, recorded_by, supersedes_version_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING version_id
        """,
        (portfolio_id, v["effective_from"], v["method"], v["rebalance_frequency"],
         v["change_type"], v["reason"], support_doc or None, recorded_by or None,
         supersedes_version_id))
    version_id = cur.fetchone()["version_id"]
    for c in v["constituents"]:
        cur.execute(
            """
            INSERT INTO benchmark_constituent
                (version_id, series_id, label, weight, fx_series_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (version_id, c["series_id"], c["label"], c["weight"], c["fx_series_id"]))
    logger.info(
        f"{bench['procode']}: version {version_id} ({v['change_type']}, {v['method']}) "
        f"effective {v['effective_from']} with {len(v['constituents'])} constituent(s).")
    return version_id


# ---- Versions: readers ---------------------------------------------------

def versions(conn, portfolio_id: int) -> list[dict]:
    """Every version of one benchmark, oldest first (by version_id)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT version_id, portfolio_id, effective_from, method, rebalance_frequency,
               change_type, reason, support_doc, recorded_by, supersedes_version_id,
               created_at
        FROM dim_benchmark_version
        WHERE portfolio_id = %s
        ORDER BY version_id
        """,
        (portfolio_id,))
    return cur.fetchall()


def governing_versions(conn, portfolio_id: int) -> list[dict]:
    """
    The versions that govern, in effective order: per effective_from
    the highest version_id, sorted by effective_from. This is the
    chain materialize() walks.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT ON (effective_from)
               version_id, portfolio_id, effective_from, method, rebalance_frequency,
               change_type, reason, supersedes_version_id, created_at
        FROM dim_benchmark_version
        WHERE portfolio_id = %s
        ORDER BY effective_from, version_id DESC
        """,
        (portfolio_id,))
    return cur.fetchall()


def constituents(conn, version_id: int) -> list[dict]:
    """The basket of one version, with the entity behind each series."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.version_id, c.series_id, c.label, c.weight, c.fx_series_id,
               sr.entity_id, e.procode, sr.field, sr.source
        FROM benchmark_constituent c
        JOIN series_registry sr ON sr.series_id = c.series_id
        JOIN dim_entity e ON e.entity_id = sr.entity_id
        WHERE c.version_id = %s
        ORDER BY c.label
        """,
        (version_id,))
    return cur.fetchall()


def changelog(conn, portfolio_id: int) -> list[dict]:
    """vw_benchmark_changelog for one benchmark, oldest version first."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT version_id, previous_version_id, effective_from, method, change_type,
               reason, created_at, series_id, label, weight_before, weight_after,
               weight_delta, change
        FROM vw_benchmark_changelog
        WHERE portfolio_id = %s
        ORDER BY version_id, change, label
        """,
        (portfolio_id,))
    return cur.fetchall()


def resolve_series_id(conn, ref) -> int:
    """
    A constituent reference from a definition file -> series_id. Either
    an integer (taken as series_id) or {'procode', 'field', 'source'}
    (field defaults to PX_LAST), resolved through series_registry.
    """
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        return int(ref)
    if not isinstance(ref, dict) or not ref.get("procode") or not ref.get("source"):
        raise ValueError(
            f"A series reference is a series_id or {{procode, field, source}}, got {ref!r}.")
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sr.series_id
        FROM series_registry sr
        JOIN dim_entity e ON e.entity_id = sr.entity_id
        WHERE e.procode = %s AND sr.field = %s AND sr.source = %s
        """,
        (ref["procode"], ref.get("field") or LEVEL_FIELD, ref["source"]))
    rows = cur.fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"{ref['procode']} / {ref.get('field') or LEVEL_FIELD} / {ref['source']} "
            f"matched {len(rows)} series; expected exactly one.")
    return rows[0]["series_id"]


def define(conn, spec: dict) -> dict:
    """
    One definition file -> ensure_benchmark + add_version. The spec:
      {"procode", "display_name", "base_currency"?,
       "version": {"effective_from", "method", "rebalance_frequency"?,
                   "change_type", "reason", "support_doc"?, "recorded_by"?,
                   "supersedes_version_id"?,
                   "constituents": [{"label", "series": <ref>, "weight",
                                     "fx_series"?: <ref>}]}}
    where <ref> is a series_id or {"procode", "field"?, "source"}.
    Returns {'portfolio_id', 'version_id', ...benchmark row}.
    """
    bench = ensure_benchmark(conn, spec.get("procode"), spec.get("display_name"),
                             spec.get("base_currency") or "PEN")
    version = spec.get("version")
    if not version:
        return {**bench, "version_id": None}
    constituents = []
    for c in version.get("constituents") or []:
        fx = c.get("fx_series")
        constituents.append({
            "label": c.get("label"),
            "series_id": resolve_series_id(conn, c.get("series")),
            "weight": c.get("weight"),
            "fx_series_id": resolve_series_id(conn, fx) if fx not in (None, "") else None,
        })
    version_id = add_version(
        conn, bench["portfolio_id"],
        effective_from=version.get("effective_from"),
        method=version.get("method"),
        change_type=version.get("change_type"),
        reason=version.get("reason"),
        constituents=constituents,
        rebalance_frequency=version.get("rebalance_frequency"),
        support_doc=version.get("support_doc"),
        recorded_by=version.get("recorded_by"),
        supersedes_version_id=version.get("supersedes_version_id"),
    )
    return {**bench, "version_id": version_id}
