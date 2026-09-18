# src/pipelines/prices/sbs/valor_cuota/run.py
# ---------------------------------------------------------------
# Orchestration for the SPP valor cuota feed. No argparse here -
# scripts/run_sbs_valor_cuota.py owns the CLI.
#
# Entry points:
#   run_daily(...)        scrape the SPP variables page (last 7
#                         business days, three metrics) and load what
#                         is missing. The only automatable load.
#                         Requires the visible Chrome + interactive
#                         session (WAF).
#   run_historico(...)    load an SBS monthly XLS (valor cuota since
#                         1993) from a path, or auto-downloaded with
#                         download=True.
#   revisar_historico()   first half of the two-step web upload: read
#                         the XLS and say exactly what would happen,
#                         writing nothing.
#   cargar_historico()    second half: write the reviewed bytes in the
#                         chosen mode (faltantes | sobrescribir).
#   correr_programado()   run_daily with a trail (extraccion.log/.json
#                         under data/spp/) for the Windows task and
#                         the dashboard's "corrida automatica" box.
#
# Unlike the file-driven SBS feeds this one does not use the
# backfill-pending dance: the series universe is closed and declared
# in config/afps.yaml, and ensure_registered() keeps the registry in
# sync on every run.
# ---------------------------------------------------------------

import logging
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from src.db.connection import get_connection
from src.db.queries import update_series_run_metadata
from src.pipelines.prices.sbs.valor_cuota import afps
from src.pipelines.prices.sbs.valor_cuota.extract import (
    load_stg, parse_daily, parse_historico, prepare_stg)
from src.pipelines.prices.sbs.valor_cuota.loader import load_facts
from src.pipelines.prices.sbs.valor_cuota.transform import transform
from src.shared.paths import DATA_DIR

logger = logging.getLogger(__name__)

MODOS_CARGA = ("faltantes", "sobrescribir")

# Trail of the unattended run, for the Windows task and the dashboard.
DIR_SPP = DATA_DIR / "spp"
ARCHIVO_REGISTRO = DIR_SPP / "extraccion.log"
ARCHIVO_ESTADO_CORRIDA = DIR_SPP / "extraccion.json"
LINEAS_REGISTRO = 400


def ensure_registered() -> None:
    """Idempotent registration of the AFP/benchmark series universe."""
    with get_connection() as conn:
        afps.register_series(conn)


@contextmanager
def candado_extraccion():
    """
    One SPP scrape at a time, ACROSS processes.

    Three paths reach the same visible Chrome and the same browser
    profile: the Windows task at 18:00, the tablero's button, and the
    .ps1's -Probar. The existing guards each cover only their own lane
    (the API's task slot is a lock inside one uvicorn process,
    -MultipleInstances only compares the task with itself), so a scrape
    launched from the tablero at 17:59 and the scheduled one a minute
    later both start: the second Chrome finds the profile's singleton,
    hands its URL to the first window and exits, and the pipeline reads
    a page that never loaded.
    """
    import os

    DIR_SPP.mkdir(parents=True, exist_ok=True)
    ruta = DIR_SPP / "extraccion.lock"
    fd = os.open(ruta, os.O_CREAT | os.O_RDWR)
    tomado = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            tomado = True
        except OSError:
            raise RuntimeError(
                "Ya hay una extraccion SPP en curso (la tarea programada, el "
                "tablero o -Probar). Espera a que termine: las dos usan el "
                "mismo Chrome y el mismo perfil.")
        os.lseek(fd, 1, os.SEEK_SET)
        os.write(fd, f"pid {os.getpid()}\n".encode())
        yield
    finally:
        try:
            if tomado:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def run_daily(run_date: Optional[date] = None, refresh: bool = False) -> dict:
    """
    Scrape -> stage -> transform -> load. Returns a small result dict
    (dates seen, fact rows loaded) for logs and the scheduler.
    """
    with candado_extraccion():
        return _run_daily(run_date, refresh)


def _run_daily(run_date: Optional[date], refresh: bool) -> dict:
    from src.scrapers import spp as scraper

    run_date = run_date or date.today()
    logger.info(f"=== prices/sbs/valor_cuota | daily | run_date={run_date} "
                f"| refresh={refresh} ===")
    ensure_registered()

    html = scraper.fetch_daily_html(run_date)
    raw_df = parse_daily(html)
    stg_df = prepare_stg(raw_df, fuente="extraccion")

    # Staging in its own transaction, committed before the fact step: a
    # later fact failure must not discard the staged rows.
    with get_connection() as conn:
        load_stg(conn, stg_df)

    return _facts_from(stg_df, refresh)


def run_historico(archivo: Optional[Path] = None, download: bool = False,
                  refresh: bool = False) -> dict:
    """
    Load the SBS historical XLS. `archivo` is a local path; with
    download=True it is fetched from the SBS index page instead
    (needs the interactive Chrome session).
    """
    logger.info(f"=== prices/sbs/valor_cuota | historico | "
                f"archivo={archivo} download={download} ===")
    ensure_registered()

    if download:
        from src.scrapers import spp as scraper
        archivo = scraper.download_historic_xls()
    if not archivo:
        raise ValueError("Falta el archivo: pasa una ruta o download=True.")

    contenido = Path(archivo).read_bytes()
    return cargar_historico(contenido,
                            modo="sobrescribir" if refresh else "faltantes")


def cargar_historico(contenido: bytes, modo: str = "faltantes") -> dict:
    """
    Writes an already-reviewed XLS in the chosen mode.

    faltantes:    inserts what the book does not have - new dates AND
                  missing cells of known dates (in long format both are
                  plain inserts on absent rows). Touches no loaded value.
    sobrescribir: additionally replaces values that differ from the
                  file. In no mode does an empty cell erase a value:
                  a missing value is a row that never reaches the loader.
    """
    if modo not in MODOS_CARGA:
        raise ValueError(f"Modo de carga no valido: {modo}. "
                         f"Usa {' o '.join(MODOS_CARGA)}.")
    # The web upload path enters here directly (no run_daily before it):
    # on a fresh database an empty series_map would silently drop every
    # value and report the load as done.
    ensure_registered()
    raw_df = parse_historico(contenido)
    # Future dates in a hand-edited file must not enter the book.
    raw_df = raw_df[raw_df["date"] <= date.today()].reset_index(drop=True)
    if raw_df.empty:
        raise ValueError("Todas las fechas del archivo son futuras.")
    stg_df = prepare_stg(raw_df, fuente="historico")

    with get_connection() as conn:
        load_stg(conn, stg_df)

    res = _facts_from(stg_df, refresh=(modo == "sobrescribir"))
    res["modo"] = modo
    return res


def _facts_from(stg_df: pd.DataFrame, refresh: bool) -> dict:
    """Shared transform+load+metadata tail of both entry points."""
    with get_connection() as conn:
        series_map = afps.series_map(conn)

    facts_df = transform(stg_df, series_map)
    if facts_df.empty:
        logger.warning("Transform returned no fact rows.")
        return {"fechas": 0, "cargadas": 0, "omitidas": 0}

    with get_connection() as conn:
        loaded, skipped = load_facts(conn, facts_df, refresh=refresh)
        # Operational trail per touched series.
        for sid, grupo in facts_df.groupby("series_id"):
            update_series_run_metadata(conn, int(sid), "success",
                                       grupo["date"].max())

    fechas = int(facts_df["date"].nunique())
    logger.info(f"=== valor_cuota complete: {fechas} fechas, "
                f"{loaded} loaded, {skipped} skipped ===")
    return {"fechas": fechas, "cargadas": loaded, "omitidas": skipped}


# ---- Review (first half of the two-step web upload) -----------------

def revisar_historico(contenido: bytes) -> dict:
    """
    Reads the XLS and says exactly what would happen, writing nothing.
    The report it returns is what the dashboard shows before choosing
    the load mode.
    """
    ensure_registered()
    no_registradas: set[str] = set()
    df = parse_historico(contenido, no_registradas)

    hoy = date.today()
    futuras = sorted(str(f) for f in df["date"].unique() if f > hoy)
    if futuras:
        df = df[df["date"] <= hoy].reset_index(drop=True)
    if df.empty:
        raise ValueError("Todas las fechas del archivo son futuras.")

    # Current book (valor cuota only - the XLS brings nothing else),
    # keyed by (afp, fondo, fecha).
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.procode, fp.date, fp.price
            FROM fact_prices fp
            JOIN series_registry sr ON sr.series_id = fp.series_id
            JOIN dim_entity e ON e.entity_id = sr.entity_id
            WHERE e.procode LIKE 'SPP\\_%%'
              AND sr.source = %s AND sr.field = 'PX_LAST'
            """,
            (afps.SOURCE_SBS,)).fetchall()
    libro: dict[tuple, float] = {}
    fechas_libro: set = set()
    for r in rows:
        parsed = afps.parse_procode(r["procode"])
        if parsed is None:
            continue
        libro[(parsed[0], parsed[1], r["date"])] = float(r["price"])
        fechas_libro.add(r["date"])

    fechas_archivo = set(df["date"])
    nuevas = fechas_archivo - fechas_libro
    conocidas = fechas_archivo & fechas_libro

    celdas_nuevas = llenaria = cambiaria = iguales = 0
    ejemplos = []
    # zip instead of iterrows: ~100k cells inside the request the user
    # is waiting on for the review report.
    for afp, fondo, fecha, v in zip(df["afp"], df["fondo"],
                                    df["date"], df["valor_cuota"]):
        v = float(v)
        if fecha in nuevas:
            celdas_nuevas += 1
            continue
        actual = libro.get((afp, int(fondo), fecha))
        if actual is None:
            llenaria += 1
        elif abs(actual - v) > 1e-9:
            cambiaria += 1
            if len(ejemplos) < 12:
                ejemplos.append({
                    "fecha": str(fecha),
                    "columna": f"{afp}_f{fondo}",
                    "serie": f"{afps.nombre_de(afp)} F{fondo}",
                    "libro": actual, "archivo": v})
        else:
            iguales += 1

    return {
        "series": int(df.groupby(["afp", "fondo"]).ngroups),
        "filas": int(df["date"].nunique()),
        "desde": str(df["date"].min()),
        "hasta": str(df["date"].max()),
        "valores": int(len(df)),
        "fechas_nuevas": len(nuevas),
        "fechas_conocidas": len(conocidas),
        "celdas_nuevas": celdas_nuevas,
        "celdas_a_llenar": llenaria,
        "celdas_distintas": cambiaria,
        "celdas_iguales": iguales,
        "ejemplos": ejemplos,
        "futuras": futuras[:10],
        "total_futuras": len(futuras),
        "no_registradas": sorted(no_registradas),
        "libro_filas": len(fechas_libro),
        "muestra": _muestra_archivo(df),
    }


def _muestra_archivo(df: pd.DataFrame, filas: int = 15) -> dict:
    """Last rows of the file in wide form, most recent first - the
    visual half of the review: counts say HOW MUCH enters, the sample
    lets you check it enters RIGHT."""
    ancho = (df.assign(col=[f"{afps.nombre_de(a)} F{f}"
                            for a, f in zip(df["afp"], df["fondo"])])
             .pivot_table(index="date", columns="col", values="valor_cuota",
                          aggfunc="last")
             .sort_index(ascending=False).head(filas))
    return {
        "columnas": list(ancho.columns),
        "filas": [[str(fecha)] +
                  [None if pd.isna(v) else float(v) for v in fila]
                  for fecha, fila in ancho.iterrows()],
        "total": int(df["date"].nunique()),
    }


# ---- Unattended run (Windows task) ----------------------------------

def _recortar_registro(lineas: int = LINEAS_REGISTRO) -> None:
    """The log must not grow forever: keep the last lines."""
    try:
        texto = ARCHIVO_REGISTRO.read_text(encoding="utf-8").splitlines()
        if len(texto) > lineas * 2:
            ARCHIVO_REGISTRO.write_text("\n".join(texto[-lineas:]) + "\n",
                                        encoding="utf-8")
    except Exception:
        pass


def correr_programado(refrescar: bool = False) -> dict:
    """
    run_daily with a written trail. Does not propagate the exception:
    the exit code already distinguishes success from failure, and what
    matters is that the failure and its reason are recorded - a run
    that fails in silence is worse than no run.
    """
    import datetime as dt
    import json

    from src.configs.machine_config import machine_id, scraper_enabled

    DIR_SPP.mkdir(parents=True, exist_ok=True)
    inicio = dt.datetime.now()
    # Each line keeps ITS OWN timestamp: a run can span hours when the
    # machine suspends mid-scrape, and stamping everything with the
    # start time turned the trail into a flat wall that hid where the
    # time actually went.
    lineas: list[tuple[float, str]] = []

    class _Anotador(logging.Handler):
        def emit(self, record):
            lineas.append((record.created, record.getMessage()))

    handler = _Anotador()
    logging.getLogger("src").addHandler(handler)

    estado = {"inicio": inicio.isoformat(timespec="seconds"),
              "accion": "extraccion programada", "refrescar": bool(refrescar)}
    try:
        # The machine gate lives INSIDE the trail on purpose: an
        # unattended run that dies before writing anything is
        # indistinguishable from one that never fired, and this is the
        # first thing to fail on a machine whose .env never declared
        # SCRAPER_ENABLED.
        if not scraper_enabled():
            raise RuntimeError(
                f"El scraper no esta habilitado en esta maquina ({machine_id()}). "
                "Pon SCRAPER_ENABLED=true en el archivo .env del proyecto.")
        res = run_daily(refresh=refrescar)
        estado.update(ok=True, error=None, **res)
    except Exception as exc:
        estado.update(ok=False, error=str(exc)[:400],
                      fechas=0, cargadas=0, omitidas=0)
        import time as _time
        lineas.append((_time.time(), f"ERROR: {exc}"))
    finally:
        logging.getLogger("src").removeHandler(handler)

    fin = dt.datetime.now()
    estado["fin"] = fin.isoformat(timespec="seconds")
    estado["segundos"] = round((fin - inicio).total_seconds(), 1)

    with open(ARCHIVO_REGISTRO, "a", encoding="utf-8") as f:
        for cuando, linea in lineas:
            f.write(f"{dt.datetime.fromtimestamp(cuando):%Y-%m-%d %H:%M:%S}  "
                    f"{linea}\n")
        f.write(f"{fin:%Y-%m-%d %H:%M:%S}  "
                f"--- fin ({'ok' if estado['ok'] else 'ERROR'} "
                f"en {estado['segundos']} s) ---\n")
    _recortar_registro()

    try:
        ARCHIVO_ESTADO_CORRIDA.write_text(
            json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return estado


def ultima_corrida() -> dict:
    """Result of the last unattended run, or empty if it never ran."""
    import json
    try:
        return json.loads(ARCHIVO_ESTADO_CORRIDA.read_text(encoding="utf-8"))
    except Exception:
        return {}
