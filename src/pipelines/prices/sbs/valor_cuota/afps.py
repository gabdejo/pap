# src/pipelines/prices/sbs/valor_cuota/afps.py
# ---------------------------------------------------------------
# AFP registry for the SPP valor cuota feed: the bridge between the
# business identities (AFP key + fund type + metric) and the
# dimensional model (dim_entity procode + series_registry field).
#
# Everything regulation can change lives in config/afps.yaml: a new
# AFP, a renamed one, or an AFP that only operates some fund types.
# This module never names a specific AFP.
#
# The design distinction everything rests on is KEY vs NAME:
#   - key names the entity procode and NEVER changes;
#   - name is display-only and can change any time.
# A corporate rename therefore touches zero historical rows.
#
# Series mapping (one entity per AFP x fund, three series each):
#   procode  SPP_{KEY}_F{fund}                 entity_type 'fund'
#   PX_LAST      valor cuota   domain 'prices'         source 'sbs'
#   CUOTAS       units         domain 'fundamentals'   source 'sbs'
#   FONDO_SOLES  fund value    domain 'fundamentals'   source 'sbs'
# PX_LAST for the NAV is deliberate: the Price Viewer charts PX_LAST
# across sources, so the funds show up there for free. Units and fund
# value are balance-sheet facts, not prices, hence 'fundamentals'
# (docs/PENDING_PIPELINES.md, item 2). No dim_security row is written:
# the funds are not instruments in the holdings universe, and the
# prices service only needs dim_entity.
# ---------------------------------------------------------------

import logging
from datetime import date
from functools import lru_cache

import yaml

from src.shared.paths import CONFIG_DIR
from src.shared.tabular import strip_accents

logger = logging.getLogger(__name__)

AFPS_CONFIG = CONFIG_DIR / "afps.yaml"

SOURCE_SBS = "sbs"

# metric name (as the SBS publishes it) -> series_registry field
METRIC_FIELD = {
    "valor_cuota": "PX_LAST",
    "cuotas": "CUOTAS",
    "fondo": "FONDO_SOLES",
}
FIELD_METRIC = {v: k for k, v in METRIC_FIELD.items()}

# series_registry field -> domain. Only the NAV is a price.
FIELD_DOMAIN = {
    "PX_LAST": "prices",
    "CUOTAS": "fundamentals",
    "FONDO_SOLES": "fundamentals",
}

# Labels shown to users (the dashboard's language is Spanish).
METRIC_LABEL = {
    "valor_cuota": "Valor cuota",
    "cuotas": "Cuotas",
    "fondo": "Fondo (S/)",
}

# The SBS historical series starts on 1993-08-02; cuotas and fondo
# only exist from the first daily-page scrape onward, but the registry
# start date is informational, not a hard floor.
DEFAULT_START = date(1993, 8, 2)


def _norm(text) -> str:
    """Normalized AFP name for comparison: no accents, upper, single spaces."""
    return " ".join(strip_accents(str(text or "")).upper().split())


def registry() -> dict:
    """
    config/afps.yaml, cached BY MODIFICATION TIME.

    The file's own header promises that editing it is enough to onboard
    an AFP, but the API is a long-lived process: with a plain cache, a
    new AFP was visible to the scheduled run (a fresh process) and
    invisible to the dashboard until someone restarted it - the two
    paths disagreeing about who exists.
    """
    try:
        mtime = AFPS_CONFIG.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return _registry(mtime)


@lru_cache(maxsize=2)
def _registry(_mtime: int) -> dict:
    """Loads and caches config/afps.yaml. Fails loud if absent or empty."""
    with open(AFPS_CONFIG, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or not data.get("afps"):
        raise ValueError(f"Invalid AFP registry at {AFPS_CONFIG}: 'afps' list required.")
    return data


def funds() -> list[int]:
    return [int(f) for f in registry().get("funds", [0, 1, 2, 3])]


def afps() -> list[dict]:
    return registry()["afps"]


def keys() -> list[str]:
    return [a["key"] for a in afps()]


def names() -> list[str]:
    return [a["name"] for a in afps()]


def _alias_map() -> dict:
    try:
        mtime = AFPS_CONFIG.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return _alias_map_cache(mtime)


@lru_cache(maxsize=2)
def _alias_map_cache(_mtime: int) -> dict:
    # Any way of naming an AFP -> its key. Includes key, current name and
    # all historical aliases, so a rename never breaks recognition of
    # older publications.
    aliases = {}
    for a in afps():
        for text in [a["key"], a["name"]] + list(a.get("aliases", [])):
            aliases[_norm(text)] = a["key"]
    return aliases


def key_of(afp) -> str | None:
    """Stable key from a key, display name, or any registered alias."""
    return _alias_map().get(_norm(afp))


def definition_of(afp) -> dict | None:
    key = key_of(afp)
    return next((a for a in afps() if a["key"] == key), None) if key else None


def name_of(afp) -> str:
    d = definition_of(afp)
    return d["name"] if d else str(afp)


def funds_of(afp) -> list[int]:
    """Fund types an AFP operates. Undeclared means all."""
    d = definition_of(afp)
    if not d:
        return funds()
    declared = d.get("funds")
    return [int(f) for f in declared] if declared else funds()


def operates(afp, fund) -> bool:
    return int(fund) in funds_of(afp)


def house_afp() -> str:
    """
    Display name of the house AFP: base of relative performance.
    Falls back to the first entry so the dashboard degrades instead
    of breaking if nobody declares the house.
    """
    for a in afps():
        if a.get("house"):
            return a["name"]
    return afps()[0]["name"]


# ---- procode naming -------------------------------------------------

def procode(afp, fund: int) -> str:
    """SPP_{KEY}_F{fund}. Derived from the key, never the name."""
    key = key_of(afp)
    if not key:
        raise ValueError(f"Unregistered AFP: {afp}. Add it to config/afps.yaml.")
    return f"SPP_{key.upper()}_F{int(fund)}"


def parse_procode(code: str) -> tuple[str, int] | None:
    """
    SPP_HABITAT_F2 -> ('habitat', 2). None when the code is not the
    procode of a registered AFP fund: other SPP_ entities (the composite
    indices of the analytics layer, for instance) share the prefix and
    must not be mistaken for a fund.
    """
    parts = str(code or "").split("_")
    if len(parts) < 3 or parts[0] != "SPP" or not parts[-1].startswith("F"):
        return None
    key = "_".join(parts[1:-1]).lower()
    if key not in keys():
        return None
    try:
        return key, int(parts[-1][1:])
    except ValueError:
        return None


# ---- DB registration ------------------------------------------------

def register_series(conn) -> int:
    """
    Idempotently registers every AFP x fund entity and its three series,
    directly as status='active'.

    Unlike the discovered SBS universe, this is a small closed set that
    afps.yaml declares in full, so there is no backfill-pending dance:
    the series exist from the moment the registry names them. A series
    whose domain drifted from FIELD_DOMAIN (registered by an earlier
    version under 'prices') is brought back, so the registry converges
    without a data migration.

    Returns the number of NEW series_registry rows inserted.
    """
    from src.db.queries import get_or_create_entity_id

    cur = conn.cursor()
    inserted = 0
    for a in afps():
        for fund in funds_of(a["key"]):
            entity_id = get_or_create_entity_id(
                conn,
                procode=procode(a["key"], fund),
                entity_type="fund",
                name=f"{a['name']} Fondo {fund}",
            )
            for field, domain in FIELD_DOMAIN.items():
                cur.execute(
                    """
                    INSERT INTO series_registry (
                        entity_id, field, domain, source, frequency,
                        default_start_date, status
                    ) VALUES (%s, %s, %s, %s, 'daily', %s, 'active')
                    ON CONFLICT (entity_id, field, source) DO NOTHING
                    """,
                    (entity_id, field, domain, SOURCE_SBS, DEFAULT_START),
                )
                if cur.rowcount > 0:
                    inserted += 1
                cur.execute(
                    """
                    UPDATE series_registry
                    SET domain = %s, updated_at = NOW()
                    WHERE entity_id = %s AND field = %s AND source = %s
                      AND domain <> %s
                    """,
                    (domain, entity_id, field, SOURCE_SBS, domain),
                )

    if inserted:
        logger.info(f"afps registry: {inserted} new series registered.")
    return inserted


def series_map(conn) -> dict[tuple[str, str], dict]:
    """
    (procode, field) -> series row for every SPP fund series. The
    runtime lookup transform and the web services share.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sr.series_id, sr.entity_id, sr.field, sr.domain, sr.source,
               e.procode, e.name
        FROM series_registry sr
        JOIN dim_entity e ON e.entity_id = sr.entity_id
        WHERE e.procode LIKE 'SPP\\_%%' AND e.entity_type = 'fund'
          AND sr.source = %s
        """,
        (SOURCE_SBS,),
    )
    return {(r["procode"], r["field"]): r for r in cur.fetchall()}
