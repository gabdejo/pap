# src/pipelines/prices/sbs/valor_cuota/afps.py
# ---------------------------------------------------------------
# AFP registry for the SPP valor cuota feed: the bridge between the
# business identities (AFP clave + fund type + metric) and the
# dimensional model (dim_entity procode + series_registry field).
#
# Everything regulation can change lives in config/afps.yaml: a new
# AFP, a renamed one, or an AFP that only operates some fund types.
# This module never names a specific AFP.
#
# The design distinction that everything rests on is CLAVE vs NOMBRE:
#   - clave names the entity procode and NEVER changes;
#   - nombre is display-only and can change any time.
# A corporate rename therefore touches zero historical rows.
#
# Series mapping (one entity per AFP x fund, one series per metric):
#   procode  SPP_{CLAVE}_F{fondo}          entity_type 'fund'
#   fields   PX_LAST (valor cuota), CUOTAS, FONDO_SOLES
#   source   'sbs'
# Composite indices (two per fund type, common to all AFPs):
#   procode  SPP_TARGET_F{fondo}  (tipo 'target')     entity_type 'index'
#            SPP_BENCH_F{fondo}   (tipo 'benchmark')  entity_type 'index'
#   field    PX_LAST                       source 'benchmark' (both: it
#            means "calculated index"; the tipo is in the procode)
# PX_LAST for the NAV is deliberate: the existing Price Viewer
# charts PX_LAST across sources, so the funds show up there for free.
# ---------------------------------------------------------------

import logging
from datetime import date
from functools import lru_cache

import yaml

from src.shared.paths import CONFIG_DIR
from src.shared.tabular import sin_tildes as _sin_tildes

logger = logging.getLogger(__name__)

AFPS_CONFIG = CONFIG_DIR / "afps.yaml"

SOURCE_SBS = "sbs"
SOURCE_BENCH = "benchmark"

# Los dos indices compuestos de cada fondo. El target es contra lo que se
# GESTIONA el fondo; el benchmark, contra lo que se MIDE. Misma maquinaria,
# distinta fila; el tipo va en el procode.
TIPOS_INDICE = ("target", "benchmark")
ETIQUETA_INDICE = {"target": "Target", "benchmark": "Benchmark"}
_PREFIJO_INDICE = {"target": "SPP_TARGET", "benchmark": "SPP_BENCH"}


def tipo_indice(tipo) -> str:
    """El tipo, validado. Todo lo que recibe un tipo pasa por aqui."""
    t = str(tipo or "").strip().lower()
    if t not in TIPOS_INDICE:
        raise ValueError(f"Tipo de indice desconocido: '{tipo}'. "
                         "Usa target o benchmark.")
    return t


def prefijo_indice(tipo) -> str:
    return _PREFIJO_INDICE[tipo_indice(tipo)]

# metric name (business, as the SBS publishes them) -> series_registry field
METRICA_FIELD = {
    "valor_cuota": "PX_LAST",
    "cuotas": "CUOTAS",
    "fondo": "FONDO_SOLES",
}
FIELD_METRICA = {v: k for k, v in METRICA_FIELD.items()}

ETIQUETA_METRICA = {
    "valor_cuota": "Valor cuota",
    "cuotas": "Cuotas",
    "fondo": "Fondo (S/)",
}

# The SBS historical series starts on 1993-08-02; cuotas and fondo
# only exist from the first daily-page extraction onward, but the
# registry start date is informational, not a hard floor.
DEFAULT_START = date(1993, 8, 2)


def _norm(t) -> str:
    """Normalized AFP name for comparison: no accents, upper, single spaces."""
    return " ".join(_sin_tildes(str(t or "")).upper().split())


def registro() -> dict:
    """
    config/afps.yaml, cached BY MODIFICATION TIME.

    The file's own header promises that editing it is enough to onboard
    an AFP, but the API is a long-lived process: with a plain cache, a
    new AFP was visible to the scheduled run (a fresh process) and
    invisible to the tablero until someone restarted it - the two paths
    disagreeing about who exists.
    """
    try:
        marca = AFPS_CONFIG.stat().st_mtime_ns
    except OSError:
        marca = 0
    return _registro(marca)


@lru_cache(maxsize=2)
def _registro(_marca: int) -> dict:
    """Loads and caches config/afps.yaml. Fails loud if absent or empty."""
    with open(AFPS_CONFIG, encoding="utf-8") as f:
        datos = yaml.safe_load(f)
    if not isinstance(datos, dict) or not datos.get("afps"):
        raise ValueError(f"Invalid AFP registry at {AFPS_CONFIG}: 'afps' list required.")
    return datos


def fondos() -> list[int]:
    return [int(f) for f in registro().get("fondos", [0, 1, 2, 3])]


def fondos_indice(tipo, conn=None) -> list[int]:
    """
    Fund types that carry an index of this tipo.

    Es la UNION de los que declara config/afps.yaml (la misma lista sirve
    a los dos tipos) y los que alguien declaro en la tabla: nombrar el
    indice de un fondo AGREGA ese fondo, nunca apaga los demas - lo
    contrario haria que ponerle nombre a uno dejara a los otros sin
    indice.
    """
    tipo = tipo_indice(tipo)
    base = [int(f) for f in registro().get("fondos_benchmark", [1, 2, 3])
            if int(f) in fondos()]
    sql = "SELECT fondo FROM benchmark WHERE tipo = %s"
    try:
        if conn is not None:
            filas = conn.execute(sql, (tipo,)).fetchall()
        else:
            from src.db.connection import get_connection
            with get_connection() as c:
                filas = c.execute(sql, (tipo,)).fetchall()
    except Exception:
        return base                # sin tabla todavia (base recien creada)
    declarados = [int(f["fondo"]) for f in filas if int(f["fondo"]) in fondos()]
    return sorted(set(base) | set(declarados))


def nombre_indice(tipo, fondo: int, conn=None) -> str:
    """El nombre que el operador le puso, o uno por defecto."""
    tipo = tipo_indice(tipo)
    porDefecto = f"{ETIQUETA_INDICE[tipo]} Fondo {int(fondo)}"
    sql = "SELECT nombre FROM benchmark WHERE tipo = %s AND fondo = %s"
    try:
        if conn is not None:
            fila = conn.execute(sql, (tipo, int(fondo))).fetchone()
        else:
            from src.db.connection import get_connection
            with get_connection() as c:
                fila = c.execute(sql, (tipo, int(fondo))).fetchone()
    except Exception:
        return porDefecto
    return (fila["nombre"] if fila and fila["nombre"] else porDefecto)


def afps() -> list[dict]:
    return registro()["afps"]


def claves() -> list[str]:
    return [a["clave"] for a in afps()]


def nombres() -> list[str]:
    return [a["nombre"] for a in afps()]


def _alias_map() -> dict:
    try:
        marca = AFPS_CONFIG.stat().st_mtime_ns
    except OSError:
        marca = 0
    return _alias_map_cache(marca)


@lru_cache(maxsize=2)
def _alias_map_cache(_marca: int) -> dict:
    # Any way of naming an AFP -> its clave. Includes clave, current name
    # and all historical aliases, so a rename never breaks recognition of
    # older publications.
    alias = {}
    for a in afps():
        for t in [a["clave"], a["nombre"]] + list(a.get("alias", [])):
            alias[_norm(t)] = a["clave"]
    return alias


def clave_de(afp) -> str | None:
    """Stable clave from a clave, display name, or any registered alias."""
    return _alias_map().get(_norm(afp))


def def_de(afp) -> dict | None:
    c = clave_de(afp)
    return next((a for a in afps() if a["clave"] == c), None) if c else None


def nombre_de(afp) -> str:
    d = def_de(afp)
    return d["nombre"] if d else str(afp)


def fondos_de(afp) -> list[int]:
    """Fund types an AFP operates. Undeclared means all."""
    d = def_de(afp)
    if not d:
        return fondos()
    declared = d.get("fondos")
    return [int(f) for f in declared] if declared else fondos()


def opera(afp, fondo) -> bool:
    return int(fondo) in fondos_de(afp)


def afp_casa() -> str:
    """
    Display name of the house AFP: base of relative performance.
    Falls back to the first entry so the dashboard degrades instead
    of breaking if nobody declares casa.
    """
    for a in afps():
        if a.get("casa"):
            return a["nombre"]
    return afps()[0]["nombre"]


# ---- procode naming -------------------------------------------------

def procode(afp, fondo: int) -> str:
    """SPP_{CLAVE}_F{fondo}. Derived from the clave, never the name."""
    c = clave_de(afp)
    if not c:
        raise ValueError(f"AFP no registrada: {afp}. Agregala en config/afps.yaml.")
    return f"SPP_{c.upper()}_F{int(fondo)}"


def procode_indice(tipo, fondo: int) -> str:
    return f"{prefijo_indice(tipo)}_F{int(fondo)}"


def procode_bench(fondo: int) -> str:
    """El benchmark, por su nombre corto. scripts/migrate_spp_history.py
    lo importa; conservarlo cuesta dos lineas."""
    return procode_indice("benchmark", fondo)


def tipo_de_procode(code: str) -> str | None:
    """'SPP_TARGET_F2' -> 'target'; None si no es un indice compuesto."""
    for tipo, prefijo in _PREFIJO_INDICE.items():
        if str(code or "").startswith(prefijo + "_F"):
            return tipo
    return None


def parse_procode(code: str) -> tuple[str, int] | None:
    """SPP_HABITAT_F2 -> ('habitat', 2). None if not an SPP fund procode."""
    parts = str(code or "").split("_")
    if len(parts) < 3 or parts[0] != "SPP" or not parts[-1].startswith("F"):
        return None
    clave = "_".join(parts[1:-1]).lower()
    # Los indices compuestos no son fondos de una AFP.
    if clave in ("bench", "target"):
        return None
    try:
        return clave, int(parts[-1][1:])
    except ValueError:
        return None


# ---- DB registration ------------------------------------------------

def register_series(conn) -> int:
    """
    Idempotently registers every AFP x fund entity and its three series,
    plus the composite-index entities (target and benchmark per fund),
    directly as status='active'.

    Unlike the discovered SBS universe, this is a small closed set that
    afps.yaml declares in full, so there is no backfill-pending dance:
    the series exist from the moment the registry names them.

    Returns the number of NEW series_registry rows inserted.
    """
    from src.db.queries import get_or_create_entity_id

    inserted = 0

    def _serie(entity_id: int, field: str, source: str) -> int:
        cur = conn.execute(
            """
            INSERT INTO series_registry (
                entity_id, field, domain, source, frequency,
                default_start_date, status
            ) VALUES (%s, %s, 'prices', %s, 'daily', %s, 'active')
            ON CONFLICT (entity_id, field, source) DO NOTHING
            """,
            (entity_id, field, source, DEFAULT_START.isoformat()),
        )
        return 1 if cur.rowcount > 0 else 0

    for a in afps():
        for f in fondos_de(a["nombre"]):
            entity_id = get_or_create_entity_id(
                conn,
                procode=procode(a["clave"], f),
                entity_type="fund",
                name=f"{a['nombre']} Fondo {f}",
            )
            conn.execute(
                """
                INSERT INTO dim_security (entity_id, security_type)
                VALUES (%s, 'fund')
                ON CONFLICT (entity_id) DO NOTHING
                """,
                (entity_id,),
            )
            for field in METRICA_FIELD.values():
                inserted += _serie(entity_id, field, SOURCE_SBS)

    for tipo in TIPOS_INDICE:
        for f in fondos_indice(tipo, conn):
            entity_id = get_or_create_entity_id(
                conn,
                procode=procode_indice(tipo, f),
                entity_type="index",
                name=nombre_indice(tipo, f, conn),
            )
            inserted += _serie(entity_id, "PX_LAST", SOURCE_BENCH)

    if inserted:
        logger.info(f"afps registry: {inserted} new series registered.")
    return inserted


def series_map(conn) -> dict[tuple[str, str], dict]:
    """
    (procode, field) -> series row for every SPP series (funds + indices).
    The runtime lookup transform and the web services share.
    """
    cur = conn.execute(
        """
        SELECT sr.series_id, sr.entity_id, sr.field, sr.source, e.procode, e.name
        FROM series_registry sr
        JOIN dim_entity e ON e.entity_id = sr.entity_id
        WHERE e.procode LIKE 'SPP\\_%' AND sr.domain = 'prices'
        """
    )
    return {(r["procode"], r["field"]): r for r in cur.fetchall()}
