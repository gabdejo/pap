# src/db/queries.py
# ---------------------------------------------------------------
# Reusable SQL query helpers (psycopg3, %s-parameterized) for the series
# registry, entities and identifiers: lookups, upserts and status filters.

import logging

from psycopg import sql
from psycopg import Connection as connection
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

# ── Entity ──────────────────────────────────────────────────────

def get_entity_id(
    conn: connection,
    procode: str,
    entity_type: str,
) -> Optional[int]:
    """
    Returns entity_id for an existing (procode, entity_type) pair,
    or None if not registered. Read-only counterpart of
    get_or_create_entity_id().
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT entity_id
        FROM dim_entity
        WHERE procode = %s AND entity_type = %s
        """,
        (procode, entity_type),
    )
    row = cur.fetchone()
    return row['entity_id'] if row else None


def get_or_create_entity_id(
    conn: connection,
    procode: str,
    entity_type: str,
    name: Optional[str] = None,
) -> int:
    """
    Returns entity_id for an existing (procode, entity_type) pair,
    or inserts a new row and returns the new entity_id.

    Race-safe: relies on uq_dim_entity_procode_type. A concurrent insert
    of the same pair makes our INSERT a no-op (ON CONFLICT DO NOTHING),
    and the final SELECT picks up the winner's row. Idempotent.
    """
    entity_id = get_entity_id(conn, procode, entity_type)
    if entity_id is not None:
        return entity_id

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO dim_entity (procode, entity_type, name)
        VALUES (%s, %s, %s)
        ON CONFLICT (procode, entity_type) DO NOTHING
        RETURNING entity_id
        """,
        (procode, entity_type, name),
    )
    row = cur.fetchone()
    if row:
        return row['entity_id']

    # Lost a concurrent race - the row exists now.
    return get_entity_id(conn, procode, entity_type)


# ── Entity identifiers ─────────────────────────────────────────

def resolve_entity_id_from_identifier(
    conn: connection,
    id_type: str,
    id_value: str,
    source: Optional[str] = None,
) -> Optional[int]:
    """
    Reverse lookup: given a vendor identifier value, returns entity_id.
    Optionally scoped by source for disambiguation.

    Returns None when not found OR when the identifier is ambiguous
    (matches more than one entity). Ambiguity is logged and left for
    manual resolution - callers make linking decisions on this result,
    so guessing an arbitrary entity would silently fork the identity graph.
    """
    cur = conn.cursor()
    if source:
        cur.execute(
            """
            SELECT DISTINCT entity_id FROM dim_entity_identifiers
            WHERE id_type = %s AND id_value = %s AND source = %s
            """,
            (id_type, id_value, source),
        )
    else:
        cur.execute(
            """
            SELECT DISTINCT entity_id FROM dim_entity_identifiers
            WHERE id_type = %s AND id_value = %s
            """,
            (id_type, id_value),
        )

    rows = cur.fetchall()
    if len(rows) == 1:
        return rows[0]['entity_id']
    if len(rows) > 1:
        logger.warning(
            f"identifier {id_type}={id_value!r} (source={source}) matched "
            f"{len(rows)} entities: {[r['entity_id'] for r in rows]}. "
            f"Refusing to resolve - fix the duplicate registration."
        )
    return None


def get_last_loaded_date(conn: connection, security_id: int, table: str):
    """
    Returns the most recent date loaded for a given security_id.
    Uses psycopg.sql.Identifier to safely inject the table name.
    """
    cur = conn.cursor()
    cur.execute(
        sql.SQL(
            """
            SELECT MAX(date) AS max_date
            FROM {tbl}
            WHERE security_id = %s
            """
        ).format(tbl=sql.Identifier(table)),
        (security_id,),
    )
    return cur.fetchone()['max_date']


def upsert_entity_identifier(
    conn: connection,
    entity_id: int,
    id_type: str,
    id_value: str,
    source: str,
    is_primary: bool = False,
    seen_date: Optional[date] = None,
) -> None:
    """
    Records an identifier OBSERVATION for an entity. The identifier table
    is MULTI-VALUED per (entity_id, id_type, source): real-world codes get
    re-issued (SBS re-codes instruments, issuers change ISINs), so every
    value ever observed persists as an alias and exactly one is flagged
    is_primary - the current code, enforced by uq_entity_identifiers_primary.
    Consumers wanting the current value join vw_entity_identifier_current;
    resolution paths query the base table so historical codes still resolve.

    Primacy rules:
    - the first value observed for a key becomes primary regardless of the
      flag (single-valued entities stay visible in the current view);
    - re-observing the primary value just refreshes its seen window;
    - a DIFFERENT value promotes only when is_primary=True AND its
      observation is not older than the current primary's last_seen_date
      (seen_date=None counts as today) - historical backfills can never
      demote the current code, which kills the old overwrite ping-pong;
    - promotion demotes the old primary to an alias and logs a WARNING.
    """
    cur = conn.cursor()

    # 1. Record / refresh the alias row. Never touches primacy on conflict.
    #    (PG LEAST/GREATEST ignore NULLs, so unknown dates merge cleanly.)
    cur.execute(
        """
        INSERT INTO dim_entity_identifiers
            (entity_id, id_type, id_value, source, is_primary,
             first_seen_date, last_seen_date)
        VALUES (%s, %s, %s, %s, FALSE, %s, %s)
        ON CONFLICT (entity_id, id_type, source, id_value) DO UPDATE SET
            first_seen_date = LEAST(dim_entity_identifiers.first_seen_date,
                                    EXCLUDED.first_seen_date),
            last_seen_date  = GREATEST(dim_entity_identifiers.last_seen_date,
                                       EXCLUDED.last_seen_date)
        """,
        (entity_id, id_type, id_value, source, seen_date, seen_date),
    )

    # 2. Resolve primacy.
    cur.execute(
        """
        SELECT id_value, last_seen_date FROM dim_entity_identifiers
        WHERE entity_id = %s AND id_type = %s AND source = %s AND is_primary
        """,
        (entity_id, id_type, source),
    )
    prim = cur.fetchone()

    if prim and prim['id_value'] == id_value:
        return                                     # already the current value

    if prim is None:
        promote = True                             # first value = current
    elif not is_primary:
        promote = False                            # alias-only observation
    else:
        observed = seen_date or date.today()
        promote = (prim['last_seen_date'] is None
                   or observed >= prim['last_seen_date'])

    if promote:
        cur.execute(
            """
            UPDATE dim_entity_identifiers SET is_primary = FALSE
            WHERE entity_id = %s AND id_type = %s AND source = %s AND is_primary
            """,
            (entity_id, id_type, source),
        )
        cur.execute(
            """
            UPDATE dim_entity_identifiers SET is_primary = TRUE
            WHERE entity_id = %s AND id_type = %s AND source = %s AND id_value = %s
            """,
            (entity_id, id_type, source, id_value),
        )
        if prim:
            logger.warning(
                f"identifier primary change entity_id={entity_id} {id_type}/{source}: "
                f"{prim['id_value']!r} -> {id_value!r} (old value kept as alias)"
            )
    elif is_primary and prim:
        logger.info(
            f"identifier alias recorded entity_id={entity_id} {id_type}/{source}: "
            f"{id_value!r} (primary stays {prim['id_value']!r})"
        )


# ── Series registry ────────────────────────────────────────────

def get_active_series(
    conn: connection,
    domain: str,
    source: str,
    frequency: str,
) -> list[dict]:
    """
    Primary runtime query for pipeline run.py files.
    Returns all active series for a domain/source/frequency combination.
    """
    query = """
        SELECT
            sr.series_id,
            sr.entity_id,
            e.procode,
            e.name,
            sr.field,
            sr.domain,
            sr.source,
            sr.frequency,
            sr.default_start_date,
            sr.release_pattern,
            sr.allow_revisions,
            sr.revision_lookback,
            bbg.id_value        AS parsekyable,
            isin.id_value       AS isin,
            codigo_sbs.id_value AS codigo_sbs
        FROM series_registry sr
        JOIN dim_entity e ON sr.entity_id = e.entity_id
        LEFT JOIN vw_entity_identifier_current bbg
            ON  bbg.entity_id = sr.entity_id
            AND bbg.id_type   = 'parsekyable'
            AND bbg.source    = 'bloomberg'
        LEFT JOIN vw_entity_identifier_current isin
            ON  isin.entity_id = sr.entity_id
            AND isin.id_type   = 'isin'
            AND isin.source    = 'internal'
        LEFT JOIN vw_entity_identifier_current codigo_sbs
            ON  codigo_sbs.entity_id = sr.entity_id
            AND codigo_sbs.id_type   = 'codigo_sbs'
            AND codigo_sbs.source    = 'sbs'
        WHERE sr.domain    = %s
          AND sr.source    = %s
          AND sr.frequency = %s
          AND sr.status    = 'active'
        ORDER BY sr.series_id
    """
    cur = conn.cursor()
    cur.execute(query, (domain, source, frequency))
    return cur.fetchall()


def get_backfill_pending_series(
    conn: connection,
    domain: Optional[str] = None,
    source: Optional[str] = None,
) -> list[dict]:
    """
    Returns all backfill-pending series with resolved identifiers.
    Optionally filtered by domain and/or source.
    """
    query = """
        SELECT
            sr.series_id,
            sr.entity_id,
            e.procode,
            e.name,
            sr.field,
            sr.domain,
            sr.source,
            sr.frequency,
            sr.default_start_date,
            sr.release_pattern,
            sr.allow_revisions,
            sr.revision_lookback,
            bbg.id_value        AS parsekyable,
            isin.id_value       AS isin,
            codigo_sbs.id_value AS codigo_sbs
        FROM series_registry sr
        JOIN dim_entity e ON sr.entity_id = e.entity_id
        LEFT JOIN vw_entity_identifier_current bbg
            ON  bbg.entity_id = sr.entity_id
            AND bbg.id_type   = 'parsekyable'
            AND bbg.source    = 'bloomberg'
        LEFT JOIN vw_entity_identifier_current isin
            ON  isin.entity_id = sr.entity_id
            AND isin.id_type   = 'isin'
            AND isin.source    = 'internal'
        LEFT JOIN vw_entity_identifier_current codigo_sbs
            ON  codigo_sbs.entity_id = sr.entity_id
            AND codigo_sbs.id_type   = 'codigo_sbs'
            AND codigo_sbs.source    = 'sbs'
        WHERE sr.status = 'backfill-pending'
    """
    params: list = []
    if domain:
        query += ' AND sr.domain = %s'
        params.append(domain)
    if source:
        query += ' AND sr.source = %s'
        params.append(source)
    query += ' ORDER BY sr.series_id'

    cur = conn.cursor()
    cur.execute(query, params)
    return cur.fetchall()


def get_suspended_series(
    conn: connection,
    domain: Optional[str] = None,
    source: Optional[str] = None,
) -> list[dict]:
    """
    Returns all series with status = suspended.
    """
    query = """
        SELECT
            sr.series_id,
            sr.entity_id,
            e.procode,
            e.name,
            sr.field,
            sr.source,
            sr.domain,
            sr.frequency,
            sr.updated_at       AS status_changed_at,
            bbg.id_value        AS parsekyable
        FROM series_registry sr
        JOIN dim_entity e
          ON sr.entity_id = e.entity_id
        LEFT JOIN vw_entity_identifier_current bbg
            ON  bbg.entity_id = sr.entity_id
            AND bbg.id_type   = 'parsekyable'
            AND bbg.source    = 'bloomberg'
        WHERE sr.status = 'suspended'
    """
    params: list = []
    if domain:
        query += " AND sr.domain = %s"
        params.append(domain)
    if source:
        query += " AND sr.source = %s"
        params.append(source)
    query += " ORDER BY sr.updated_at DESC"

    cur = conn.cursor()
    cur.execute(query, params)
    return cur.fetchall()


def get_inactive_series(
    conn: connection,
    domain: Optional[str] = None,
    source: Optional[str] = None,
) -> list[dict]:
    """
    Returns all series with status = inactive.
    """
    query = """
        SELECT
            sr.series_id,
            sr.entity_id,
            e.procode,
            e.name,
            sr.field,
            sr.source,
            sr.domain,
            sr.updated_at       AS inactivated_at,
            bbg.id_value        AS parsekyable
        FROM series_registry sr
        JOIN dim_entity e
          ON sr.entity_id = e.entity_id
        LEFT JOIN vw_entity_identifier_current bbg
            ON  bbg.entity_id = sr.entity_id
            AND bbg.id_type   = 'parsekyable'
            AND bbg.source    = 'bloomberg'
        WHERE sr.status = 'inactive'
    """
    params: list = []
    if domain:
        query += " AND sr.domain = %s"
        params.append(domain)
    if source:
        query += " AND sr.source = %s"
        params.append(source)
    query += " ORDER BY sr.updated_at DESC"

    cur = conn.cursor()
    cur.execute(query, params)
    return cur.fetchall()


def get_error_hold_series(
    conn: connection,
    domain: Optional[str] = None,
    source: Optional[str] = None,
) -> list[dict]:
    """
    Returns all series with status = error-hold.
    """
    query = """
        SELECT
            sr.series_id,
            sr.entity_id,
            e.procode,
            e.name,
            sr.field,
            sr.source,
            sr.domain,
            sr.last_run_at,
            sr.updated_at       AS error_at,
            bbg.id_value        AS parsekyable
        FROM series_registry sr
        JOIN dim_entity e
          ON sr.entity_id = e.entity_id
        LEFT JOIN vw_entity_identifier_current bbg
            ON  bbg.entity_id = sr.entity_id
            AND bbg.id_type   = 'parsekyable'
            AND bbg.source    = 'bloomberg'
        WHERE sr.status = 'error-hold'
    """
    params: list = []
    if domain:
        query += " AND sr.domain = %s"
        params.append(domain)
    if source:
        query += " AND sr.source = %s"
        params.append(source)
    query += " ORDER BY sr.updated_at DESC"

    cur = conn.cursor()
    cur.execute(query, params)
    return cur.fetchall()


def get_registered_securities(
    conn: connection,
) -> list[dict]:
    """
    Returns all securities registered in dim_security regardless
    of series_registry status, domain, source or frequency.
    Used exclusively by dim enrichment pipelines.
    Each security appears once – deduplication is by entity_id.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT
            e.entity_id,
            e.procode,
            e.name,
            ds.security_type,
            bbg.id_value AS parsekyable
        FROM dim_entity e
        JOIN dim_security ds ON ds.entity_id = e.entity_id
        LEFT JOIN vw_entity_identifier_current bbg
            ON  bbg.entity_id = e.entity_id
            AND bbg.id_type   = 'parsekyable'
            AND bbg.source    = 'bloomberg'
        WHERE e.entity_type = 'security'
        ORDER BY e.entity_id
        """
    )
    return cur.fetchall()


def update_series_status(
    conn: connection,
    series_id: int,
    status: str,
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE series_registry
        SET status     = %s,
            updated_at = NOW()
        WHERE series_id = %s
        """,
        (status, series_id),
    )


def update_series_run_metadata(
    conn: connection,
    series_id: int,
    run_status: str,
    last_loaded_date: Optional[date] = None,
) -> None:
    """
    Called by loaders after each pipeline run to track operational state.
    """
    cur = conn.cursor()
    # GREATEST, not COALESCE: last_loaded_date is a high-water mark of
    # coverage. Loading an OLDER batch (a historical file behind current
    # coverage) must never regress it; GREATEST ignores a NULL argument,
    # so passing None still keeps the existing value.
    cur.execute(
        """
        UPDATE series_registry
        SET last_run_at       = NOW(),
            last_run_status   = %s,
            last_loaded_date  = GREATEST(%s, last_loaded_date),
            updated_at        = NOW()
        WHERE series_id = %s
        """,
        (
            run_status,
            last_loaded_date,
            series_id,
        ),
    )


# ── Fact tables ─────────────────────────────────────────────────

def get_last_price_date(
    conn: connection,
    series_id: int,
) -> Optional[date]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MAX(date) AS last_date
        FROM fact_prices
        WHERE series_id = %s
        """,
        (series_id,),
    )
    row = cur.fetchone()
    if row and row['last_date']:
        return row['last_date']
    return None
