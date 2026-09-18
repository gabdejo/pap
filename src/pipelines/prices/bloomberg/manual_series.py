# src/pipelines/prices/bloomberg/manual_series.py
# ---------------------------------------------------------------
# Manual Bloomberg series registry + download, ported from the SPP
# monitor's bloomberg.py onto this project's stack (psycopg,
# machine_config gate).
#
# Deliberately separate from the pipeline-driven Bloomberg feed in
# this same package: that one is series_registry-spine, batch,
# scheduler-owned; this one is a hand-curated registry managed from
# the dashboard (ad-hoc tickers, mixed frequencies) whose data lands
# in bloomberg_serie / bloomberg_dato, both in LONG format - adding
# a ticker is an INSERT, not a schema change.
#
# blpapi is only installed on terminal machines and never imported
# at module load: the dashboard must start on machines without a
# terminal, with this module announcing itself unavailable instead
# of crashing.
# ---------------------------------------------------------------

import datetime as dt
import io
import logging
import re
from typing import Iterable, Optional

import pandas as pd

from src.configs.machine_config import bloomberg_enabled, machine_id
from src.db.connection import get_connection
from src.pipelines.prices.sbs.valor_cuota.benchmark_composicion import (motivo_en_uso, usos_de)
from src.shared import tabular

logger = logging.getLogger(__name__)

SERVICIO = "//blp/refdata"
PETICION_HISTORICA = "HistoricalDataRequest"

# Offered intervals, and how Bloomberg names them. The registry keeps
# the Spanish form; the request uses the right-hand one.
INTERVALOS = {
    "diario": "DAILY",
    "semanal": "WEEKLY",
    "mensual": "MONTHLY",
    "trimestral": "QUARTERLY",
    "semestral": "SEMI_ANNUALLY",
    "anual": "YEARLY",
}

# Points-per-run cap: Bloomberg limits daily historical quota, so a
# large initial load is better split across runs than starved.
TOPE_PUNTOS = 300_000

CAMPO_POR_DEFECTO = "PX_LAST"


# ---- Normalizers ----------------------------------------------------

def _norm_intervalo(valor) -> str:
    v = str(valor or "diario").strip().lower()
    if v in INTERVALOS:
        return v
    for clave, bbg in INTERVALOS.items():
        if v.upper() == bbg:
            return clave
    raise ValueError(f"Intervalo no valido: {valor}. Usa {', '.join(INTERVALOS)}.")


def _norm_ticker(valor) -> str:
    """
    Canonical Bloomberg ticker: '<PAPEL> <Yellow key>' with the paper
    uppercased and the yellow key capitalized, so 'spx index' and
    'SPX Index' never coexist as two registry rows.
    """
    partes = str(valor or "").split()
    if not partes:
        raise ValueError("El ticker no puede estar vacio.")
    t = " ".join([p.upper() for p in partes[:-1]] + [partes[-1].capitalize()])
    if len(t) > 64:
        raise ValueError(f"Ticker demasiado largo: {t[:70]}")
    return t


def clave_ticker(valor) -> str:
    return _norm_ticker(valor).upper()


def _norm_campo(valor) -> str:
    c = re.sub(r"\s+", "", str(valor or "")).upper() or CAMPO_POR_DEFECTO
    if not re.fullmatch(r"[A-Z0-9_]{1,48}", c):
        raise ValueError(f"Campo no valido: {valor}")
    return c


def _fila_a_texto(fila: dict) -> dict:
    """Dates and timestamps out as text, for JSON."""
    salida = dict(fila)
    for k in ("fecha_inicio", "ultima_fecha", "desde", "hasta"):
        if salida.get(k):
            salida[k] = str(salida[k])
    for k in ("ultimo_intento", "creada_en"):
        if salida.get(k):
            salida[k] = salida[k].isoformat(timespec="seconds")
    return salida


# ---- Registry -------------------------------------------------------

def registrar_serie(ticker, campo=CAMPO_POR_DEFECTO, intervalo="diario",
                    descripcion=None, moneda=None, fecha_inicio=None,
                    activa=None, conn=None) -> dict:
    """
    Upserts a series by (folded ticker, campo, intervalo). What is not
    sent is not erased: redeclaring a series to fix its description
    must not reset its start date nor silently reactivate a paused one.

    :param activa: True/False to change it; None keeps the current
                   state. A new series is born active.
    :param conn: Open connection to reuse (bulk registration passes one
                 for the whole file instead of opening one per row).
    """
    ticker = _norm_ticker(ticker)
    campo = _norm_campo(campo)
    intervalo = _norm_intervalo(intervalo)
    inicio = pd.to_datetime(fecha_inicio).date() if fecha_inicio else None
    if inicio and inicio > dt.date.today():
        raise ValueError("La fecha de inicio no puede ser futura.")

    if conn is None:
        with get_connection() as propia:
            return registrar_serie(ticker, campo, intervalo, descripcion,
                                   moneda, fecha_inicio, activa, conn=propia)

    conn.execute(
        """
        INSERT INTO bloomberg_serie (
            ticker, ticker_clave, campo, intervalo, descripcion,
            moneda, fecha_inicio, activa
        ) VALUES (%(ticker)s, %(clave)s, %(campo)s, %(intervalo)s,
                  %(descripcion)s, %(moneda)s, %(inicio)s, %(activa)s)
        ON CONFLICT (ticker_clave, campo, intervalo) DO UPDATE SET
            ticker       = EXCLUDED.ticker,
            descripcion  = COALESCE(EXCLUDED.descripcion, bloomberg_serie.descripcion),
            moneda       = COALESCE(EXCLUDED.moneda, bloomberg_serie.moneda),
            fecha_inicio = COALESCE(EXCLUDED.fecha_inicio, bloomberg_serie.fecha_inicio),
            activa       = CASE WHEN %(cambia_activa)s
                                THEN EXCLUDED.activa
                                ELSE bloomberg_serie.activa END
        """,
        {
            "ticker": ticker, "clave": clave_ticker(ticker),
            "campo": campo, "intervalo": intervalo,
            "descripcion": descripcion, "moneda": moneda, "inicio": inicio,
            "activa": True if activa is None else bool(activa),
            "cambia_activa": activa is not None,
        },
    )
    fila = conn.execute(
        """
        SELECT * FROM bloomberg_serie
        WHERE ticker_clave = %s AND campo = %s AND intervalo = %s
        """,
        (clave_ticker(ticker), campo, intervalo),
    ).fetchone()
    return _fila_a_texto(fila) if fila else {}


def borrar_serie(serie_id: int, con_datos: bool = False) -> dict:
    """
    Removes a series. Refuses while a benchmark composition still
    references it, and refuses when it has data unless con_datos=True:
    deleting a loaded series should be deliberate, not a stray click.
    """
    with get_connection() as conn:
        # benchmark_composicion.ref_id is polymorphic (it points at either
        # store), so there is no foreign key to stop this. Without the
        # check the composition row survives and the next recalculation
        # fails with "X no tiene precio en o antes del rebalanceo" - a
        # diagnosis that sends the operator looking for missing prices
        # instead of the series they deleted. The manual store already
        # guards this; both stores have to behave the same way.
        vigentes, historicos = usos_de(conn, "bloomberg", serie_id)
        if vigentes or historicos:
            fila = conn.execute(
                "SELECT ticker, campo FROM bloomberg_serie WHERE serie_id = %s",
                (serie_id,)).fetchone()
            nombre = f"{fila['ticker']} {fila['campo']}" if fila else serie_id
            raise ValueError(motivo_en_uso(nombre, vigentes, historicos))

        n = conn.execute(
            "SELECT COUNT(*) AS n FROM bloomberg_dato WHERE serie_id = %s",
            (serie_id,),
        ).fetchone()["n"]
        if n and not con_datos:
            raise ValueError(
                f"La serie tiene {n:,} dato(s) cargados. Confirma el borrado "
                "de los datos si es lo que quieres.")
        conn.execute("DELETE FROM bloomberg_dato WHERE serie_id = %s", (serie_id,))
        conn.execute("DELETE FROM bloomberg_serie WHERE serie_id = %s", (serie_id,))
    return {"serie_id": serie_id, "datos_borrados": int(n)}


def series_registro(conn=None) -> list[dict]:
    """
    The registry WITHOUT the coverage aggregation - for callers that
    only need labels (ticker/campo/intervalo). series() LEFT JOINs and
    aggregates the whole data table, which is a full-table scan once
    bulk history is loaded; labeling a chart must not pay that.
    """
    sql = "SELECT * FROM bloomberg_serie ORDER BY ticker, campo, intervalo"
    if conn is not None:
        filas = conn.execute(sql).fetchall()
    else:
        with get_connection() as propia:
            filas = propia.execute(sql).fetchall()
    return [_fila_a_texto(f) for f in filas]


def series(solo_activas: bool = False) -> list[dict]:
    """The full registry with each series' real coverage."""
    sql = """
        SELECT s.*, COUNT(d.fecha) AS filas,
               MIN(d.fecha) AS desde, MAX(d.fecha) AS hasta
        FROM bloomberg_serie s
        LEFT JOIN bloomberg_dato d ON d.serie_id = s.serie_id
    """
    if solo_activas:
        sql += " WHERE s.activa"
    sql += " GROUP BY s.serie_id ORDER BY s.ticker, s.campo, s.intervalo"
    with get_connection() as conn:
        filas = conn.execute(sql).fetchall()
    return [_fila_a_texto(f) for f in filas]


def leer(serie_ids: Optional[Iterable[int]] = None,
         desde=None, hasta=None) -> pd.DataFrame:
    """Data points in long format: serie_id, fecha, valor."""
    sql = "SELECT serie_id, fecha, valor FROM bloomberg_dato"
    conds, params = [], []
    if serie_ids:
        ids = list(serie_ids)
        conds.append(f"serie_id IN ({','.join('%s' for _ in ids)})")
        params += ids
    if desde:
        conds.append("fecha >= %s")
        params.append(pd.to_datetime(desde).date())
    if hasta:
        conds.append("fecha <= %s")
        params.append(pd.to_datetime(hasta).date())
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY fecha"
    with get_connection() as conn:
        filas = conn.execute(sql, params).fetchall()
    df = pd.DataFrame(filas) if filas else pd.DataFrame(
        columns=["serie_id", "fecha", "valor"])
    if not df.empty:
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    return df


def estado() -> dict:
    """Summary for the dashboard tab."""
    filas = series()
    puede, motivo = disponible()
    return {
        "disponible": puede,
        "motivo": None if puede else motivo,
        "series": len(filas),
        "activas": len([f for f in filas if f["activa"]]),
        # Sum of the per-series counts the coverage query already
        # aggregated - the FK guarantees no orphan rows exist.
        "datos": int(sum(int(f["filas"] or 0) for f in filas)),
        "intervalos": list(INTERVALOS),
        "detalle": filas,
    }


# ---- blpapi client --------------------------------------------------

def disponible() -> tuple[bool, str]:
    """
    Whether THIS machine can talk to Bloomberg: the machine_config
    gate first, then the library.
    """
    if not bloomberg_enabled():
        return False, (f"Bloomberg no esta habilitado en esta maquina "
                       f"({machine_id()}). Activa bloomberg_enabled en "
                       "machine_config.local.yaml si tiene terminal.")
    try:
        import blpapi  # noqa: F401
    except ImportError:
        return False, ("Falta la libreria blpapi. Se instala solo donde hay "
                       "terminal:\n  pip install --index-url "
                       "https://blpapi.bloomberg.com/repository/releases/python/simple/"
                       " blpapi")
    return True, "Bloomberg disponible."


def pedir_historico(tickers: list, campos: list, desde, hasta,
                    intervalo: str = "diario", opciones: dict | None = None,
                    log=logger.info) -> pd.DataFrame:
    """
    One BDH request: several tickers and fields in a single call.
    Returns long format [ticker, campo, fecha, valor]. The session is
    opened and closed inside the request - runs are sporadic and a
    hung session is a silent way for everything to stop working.
    """
    puede, motivo = disponible()
    if not puede:
        raise RuntimeError(motivo)
    import blpapi

    intervalo = _norm_intervalo(intervalo)
    tickers = [_norm_ticker(t) for t in tickers]
    campos = [_norm_campo(c) for c in campos]
    d0 = pd.to_datetime(desde).date()
    d1 = pd.to_datetime(hasta).date()

    log(f"Bloomberg: {len(tickers)} ticker(s) x {len(campos)} campo(s) "
        f"| {intervalo} | {d0} a {d1}")

    sesion = blpapi.Session()
    try:
        if not sesion.start():
            raise RuntimeError("No se pudo iniciar la sesion de Bloomberg.")
        if not sesion.openService(SERVICIO):
            raise RuntimeError(f"No se pudo abrir {SERVICIO}.")

        peticion = sesion.getService(SERVICIO).createRequest(PETICION_HISTORICA)
        peticion[blpapi.Name("securities")] = tickers
        peticion[blpapi.Name("fields")] = campos
        peticion[blpapi.Name("startDate")] = d0.strftime("%Y%m%d")
        peticion[blpapi.Name("endDate")] = d1.strftime("%Y%m%d")
        # Periodicity is part of the request, not a later filter: asking
        # quarterly and receiving daily would burn quota for nothing.
        peticion[blpapi.Name("periodicitySelection")] = INTERVALOS[intervalo]
        peticion[blpapi.Name("periodicityAdjustment")] = "CALENDAR"
        for k, v in (opciones or {}).items():
            peticion[blpapi.Name(k)] = v

        sesion.sendRequest(peticion)

        registros, avisos = [], []
        while True:
            evento = sesion.nextEvent()
            for mensaje in evento:
                if mensaje.messageType() == blpapi.Names.REQUEST_FAILURE:
                    raise RuntimeError("Bloomberg rechazo la peticion: "
                                       f"{str(mensaje)[:300]}")
                cuerpo = mensaje.toPy()
                datos = cuerpo.get("securityData") if isinstance(cuerpo, dict) else None
                if datos is None:
                    continue
                for sec in (datos if isinstance(datos, list) else [datos]):
                    ticker = sec.get("security")
                    # toPy() delivers securityError as a single dict, not a
                    # list; iterating it bare would log its KEYS. Normalize
                    # and surface the actual message.
                    errores = sec.get("securityError") or []
                    if isinstance(errores, dict):
                        errores = [errores]
                    for err in errores:
                        motivo = (err.get("message") or err.get("category")
                                  or str(err)) if isinstance(err, dict) else str(err)
                        avisos.append(f"{ticker}: {motivo}")
                    campos_datos = sec.get("fieldData") or []
                    if isinstance(campos_datos, dict):
                        campos_datos = [campos_datos]
                    for punto in campos_datos:
                        fecha = punto.get("date")
                        if fecha is None:
                            continue
                        for campo in campos:
                            valor = punto.get(campo)
                            if valor is None:
                                continue
                            try:
                                valor = float(valor)
                            except (TypeError, ValueError):
                                continue
                            registros.append({"ticker": ticker, "campo": campo,
                                              "fecha": fecha, "valor": valor})
            if evento.eventType() == blpapi.Event.RESPONSE:
                break
    finally:
        try:
            sesion.stop()
        except Exception:
            pass

    for a in avisos:
        log(f"Aviso de Bloomberg: {a}")

    if not registros:
        log("Bloomberg no devolvio datos para esa peticion.")
        return pd.DataFrame(columns=["ticker", "campo", "fecha", "valor"])

    df = pd.DataFrame(registros)
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.date
    log(f"Bloomberg devolvio {len(df):,} observaciones.")
    return df


# ---- Persistence ----------------------------------------------------

def guardar(serie_id: int, puntos: pd.DataFrame, corregir: bool = False,
            log=logger.info) -> dict:
    """
    Writes one series' points. Default inserts only what is missing;
    corregir=True also replaces, for when Bloomberg restates a figure.
    """
    if puntos is None or puntos.empty:
        return {"nuevos": 0, "actualizados": 0, "sin_cambio": 0, "total": 0}

    filas = []
    for fecha, valor in zip(puntos["fecha"], puntos["valor"]):
        if valor is None or pd.isna(valor):
            continue
        filas.append((int(serie_id), pd.to_datetime(fecha).date(), float(valor)))
    if not filas:
        return {"nuevos": 0, "actualizados": 0, "sin_cambio": 0, "total": 0}

    if corregir:
        stmt = """
            INSERT INTO bloomberg_dato (serie_id, fecha, valor)
            VALUES (%s, %s, %s)
            ON CONFLICT (serie_id, fecha) DO UPDATE SET
                valor = EXCLUDED.valor, actualizado_en = NOW()
        """
    else:
        stmt = """
            INSERT INTO bloomberg_dato (serie_id, fecha, valor)
            VALUES (%s, %s, %s)
            ON CONFLICT (serie_id, fecha) DO NOTHING
        """
    with get_connection() as conn:
        if corregir:
            # DO UPDATE reports rowcount=1 for both paths, so splitting
            # nuevos/actualizados still needs the existing dates - but
            # only this branch pays for that fetch.
            previas = {r["fecha"] for r in conn.execute(
                "SELECT fecha FROM bloomberg_dato WHERE serie_id = %s",
                (serie_id,)).fetchall()}
            cur = conn.cursor()
            cur.executemany(stmt, filas)
            nuevos = len([f for f in filas if f[1] not in previas])
        else:
            # DO NOTHING: the aggregate rowcount IS the inserted count.
            cur = conn.cursor()
            cur.executemany(stmt, filas)
            nuevos = cur.rowcount if cur.rowcount >= 0 else 0
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM bloomberg_dato WHERE serie_id = %s",
            (serie_id,)).fetchone()["n"]

    res = {"nuevos": nuevos,
           "actualizados": (len(filas) - nuevos) if corregir else 0,
           "sin_cambio": 0 if corregir else (len(filas) - nuevos),
           "total": int(total)}
    log(f"Serie {serie_id}: {res['nuevos']} nuevos, {res['actualizados']} "
        f"actualizados, {res['sin_cambio']} ya estaban (total {res['total']:,}).")
    return res


def _marcar(serie_id: int, resultado: str, error=None, ultima_fecha=None) -> None:
    """Leaves the run trail on the registry row and recounts its points."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE bloomberg_serie SET
                ultimo_intento = NOW(),
                ultimo_resultado = %s,
                ultimo_error = %s,
                ultima_fecha = COALESCE(%s, ultima_fecha),
                puntos = (SELECT COUNT(*) FROM bloomberg_dato
                          WHERE serie_id = %s)
            WHERE serie_id = %s
            """,
            (resultado, str(error)[:400] if error else None,
             ultima_fecha, serie_id, serie_id),
        )


# ---- Extraction -----------------------------------------------------

def _ventana(serie: dict, hasta: dt.date, corregir: bool = False):
    """
    Request window per series: a new one starts at its declared date, a
    loaded one the day after its last point, and an up-to-date one
    returns None and burns no quota.

    corregir re-requests the WHOLE declared window: a corrective run
    exists to re-fetch figures Bloomberg restated, and starting after
    the last stored point would never touch an already-loaded date -
    the DO UPDATE branch in guardar() would be dead code.
    """
    ultima = None if corregir else (serie.get("hasta") or serie.get("ultima_fecha"))
    if ultima:
        desde = pd.to_datetime(ultima).date() + dt.timedelta(days=1)
    elif serie.get("fecha_inicio"):
        desde = pd.to_datetime(serie["fecha_inicio"]).date()
    else:
        desde = dt.date(2000, 1, 1)
    return None if desde > hasta else (desde, hasta)


def extraer(serie_ids=None, hasta=None, corregir: bool = False,
            log=logger.info) -> dict:
    """
    Downloads what is missing for the active series. Requests are
    grouped by (desde, intervalo, campo) - the interval is part of the
    key on purpose: mixing a quarterly series with a daily one in the
    same call returns the wrong periodicity for one of them, silently.
    """
    if not bloomberg_enabled():
        raise RuntimeError(
            f"Bloomberg no esta habilitado en esta maquina ({machine_id()}). "
            "Activa bloomberg_enabled en machine_config.local.yaml.")

    hasta = pd.to_datetime(hasta).date() if hasta else dt.date.today()
    pedidas = set(serie_ids) if serie_ids else None
    registro = [s for s in series(solo_activas=True)
                if pedidas is None or s["serie_id"] in pedidas]
    if not registro:
        log("No hay series activas que bajar.")
        return {"series": 0, "peticiones": 0, "nuevos": 0, "al_dia": 0,
                "detalle": []}

    grupos: dict[tuple, list] = {}
    al_dia = []
    for s in registro:
        ventana = _ventana(s, hasta, corregir=corregir)
        if ventana is None:
            al_dia.append(s["serie_id"])
            continue
        grupos.setdefault((ventana[0], s["intervalo"], s["campo"]), []).append(s)

    if al_dia:
        log(f"{len(al_dia)} serie(s) ya estaban al dia.")
    if not grupos:
        return {"series": len(registro), "peticiones": 0, "nuevos": 0,
                "al_dia": len(al_dia), "detalle": []}

    log(f"{sum(len(g) for g in grupos.values())} serie(s) en "
        f"{len(grupos)} peticion(es).")

    detalle, nuevos_total, puntos_pedidos = [], 0, 0

    for (desde, intervalo, campo), grupo in sorted(grupos.items()):
        tickers = sorted({s["ticker"] for s in grupo})

        if puntos_pedidos >= TOPE_PUNTOS:
            log(f"Se alcanzo el tope de {TOPE_PUNTOS:,} puntos por corrida; "
                "el resto queda para la siguiente.")
            for s in grupo:
                detalle.append({"serie_id": s["serie_id"], "ticker": s["ticker"],
                                "campo": campo, "intervalo": intervalo,
                                "estado": "pospuesta", "nuevos": 0})
            continue

        try:
            df = pedir_historico(tickers, [campo], desde, hasta,
                                 intervalo=intervalo, log=log)
        except Exception as exc:
            log(f"ERROR en la peticion ({intervalo}, {campo}, desde {desde}): {exc}")
            for s in grupo:
                _marcar(s["serie_id"], "error", str(exc))
                detalle.append({"serie_id": s["serie_id"], "ticker": s["ticker"],
                                "campo": campo, "intervalo": intervalo,
                                "estado": "error", "nuevos": 0,
                                "motivo": str(exc)[:200]})
            continue

        puntos_pedidos += len(df)

        for s in grupo:
            suyo = df[df["ticker"] == s["ticker"]] if not df.empty else df
            if suyo.empty:
                _marcar(s["serie_id"], "vacio")
                detalle.append({"serie_id": s["serie_id"], "ticker": s["ticker"],
                                "campo": campo, "intervalo": intervalo,
                                "estado": "sin datos", "nuevos": 0})
                continue
            res = guardar(s["serie_id"], suyo[["fecha", "valor"]],
                          corregir=corregir, log=lambda m: None)
            nuevos_total += res["nuevos"]
            _marcar(s["serie_id"], "ok", ultima_fecha=max(suyo["fecha"]))
            detalle.append({"serie_id": s["serie_id"], "ticker": s["ticker"],
                            "campo": campo, "intervalo": intervalo,
                            "estado": "ok", "nuevos": res["nuevos"],
                            "hasta": str(max(suyo["fecha"]))})

    con_datos = len([d for d in detalle if d.get("nuevos")])
    log(f"Bloomberg: {nuevos_total:,} punto(s) nuevos en {con_datos} serie(s).")
    return {"series": len(registro), "peticiones": len(grupos),
            "nuevos": nuevos_total, "al_dia": len(al_dia), "detalle": detalle}


# ---- Bulk registration by file --------------------------------------
# The file declares WHAT to download, not the data - Bloomberg brings
# that later. Only `ticker` is required; PX_LAST and diario are the
# defaults.

CABECERA_SERIES = ["ticker", "campo", "intervalo", "fecha_inicio",
                   "descripcion", "moneda"]

_ALIAS_COL = {
    "ticker": "ticker", "bbgticker": "ticker", "tickerbloomberg": "ticker",
    "papel": "ticker", "security": "ticker",
    "campo": "campo", "field": "campo", "mnemonico": "campo", "flds": "campo",
    "intervalo": "intervalo", "frecuencia": "intervalo",
    "periodicidad": "intervalo", "interval": "intervalo", "frequency": "intervalo",
    "fechainicio": "fecha_inicio", "desde": "fecha_inicio",
    "inicio": "fecha_inicio", "startdate": "fecha_inicio",
    "descripcion": "descripcion", "nombre": "descripcion", "detalle": "descripcion",
    "moneda": "moneda", "currency": "moneda", "crncy": "moneda",
}


def leer_archivo_series(datos, hoja=None) -> dict:
    """
    Reads an Excel/CSV listing series to register, without touching the
    DB. Returns what was read and what was discarded, so it can be
    shown before anything is registered.
    """
    if tabular.es_excel(datos):
        filas, nombre_hoja, _ = tabular.filas_de_excel(datos, hoja)
        origen = "excel"
    else:
        filas, _, _ = tabular.filas_de_csv(datos)
        origen, nombre_hoja = "csv", None

    icab = tabular.fila_cabecera(
        filas, lambda c: _ALIAS_COL.get(tabular.clave_col(c)) == "ticker")
    if icab is None:
        vistas = [str(c) for f in filas[:3] for c in f
                  if str(c).strip() and str(c) != "nan"]
        raise ValueError(
            "Falta la columna 'ticker'. No se encontro una fila de encabezados "
            f"en las primeras filas. Se leyo: {', '.join(vistas[:10]) or 'nada'}")

    cabecera = tabular.limpiar_cabecera(filas[icab])
    porcol, avisos = {}, []
    for i, c in enumerate(cabecera):
        if not c:
            continue
        destino = _ALIAS_COL.get(tabular.clave_col(c))
        if destino:
            porcol.setdefault(destino, i)
        else:
            avisos.append(f"Se ignoro la columna '{c}': no corresponde a "
                          f"ninguna de {', '.join(CABECERA_SERIES)}.")
    if icab:
        avisos.append(f"Los encabezados estaban en la fila {icab + 1}; "
                      "lo anterior se ignoro.")

    def celda(fila, clave):
        i = porcol.get(clave)
        if i is None or i >= len(fila):
            return None
        v = fila[i]
        if v is None or (isinstance(v, float) and v != v):
            return None
        t = str(v).strip()
        return t or None

    leidas, omitidas, vistas = [], [], set()
    for n, fila in enumerate(filas[icab + 1:], start=icab + 2):
        if all(v is None or str(v).strip() in ("", "nan") for v in fila):
            continue
        try:
            ticker = _norm_ticker(celda(fila, "ticker"))
            campo = _norm_campo(celda(fila, "campo") or CAMPO_POR_DEFECTO)
            intervalo = _norm_intervalo(celda(fila, "intervalo") or "diario")
        except ValueError as exc:
            omitidas.append({"linea": n, "motivo": str(exc)[:120]})
            continue

        inicio = celda(fila, "fecha_inicio")
        if inicio is not None:
            f = tabular.fecha_flexible(inicio)
            if f is None:
                omitidas.append({"linea": n,
                                 "motivo": f"fecha de inicio ilegible: {str(inicio)[:24]}"})
                continue
            if f > dt.date.today():
                omitidas.append({"linea": n, "motivo": f"fecha de inicio futura: {f}"})
                continue
            inicio = f

        clave = (clave_ticker(ticker), campo, intervalo)
        if clave in vistas:
            omitidas.append({"linea": n,
                             "motivo": f"repetida en el archivo: {ticker} / {campo} / {intervalo}"})
            continue
        vistas.add(clave)
        leidas.append({"ticker": ticker, "campo": campo, "intervalo": intervalo,
                       "fecha_inicio": str(inicio) if inicio else None,
                       "descripcion": celda(fila, "descripcion"),
                       "moneda": celda(fila, "moneda")})

    if not leidas:
        raise ValueError("No se pudo leer ninguna serie valida. Se esperaba "
                         "al menos una columna 'ticker' con contenido.")

    return {"origen": origen, "hoja": nombre_hoja,
            "columnas": sorted(porcol), "series": leidas,
            "total": len(leidas), "avisos": avisos,
            "omitidas": omitidas[:25], "total_omitidas": len(omitidas),
            "intervalos": sorted({s["intervalo"] for s in leidas}),
            "campos": sorted({s["campo"] for s in leidas})}


def registrar_series_archivo(datos, hoja=None, log=logger.info) -> dict:
    """Reads the file and registers everything it brings."""
    lectura = leer_archivo_series(datos, hoja)
    log(f"Archivo {lectura['origen']}: {lectura['total']} serie(s) leidas.")
    for a in lectura["avisos"]:
        log(f"Aviso: {a}")

    # One connection for the whole file, and a keys-only previas SELECT:
    # a per-row connection plus the coverage aggregation was pure
    # handshake overhead for a bulk registration.
    with get_connection() as conn:
        previas = {(r["ticker_clave"], r["campo"], r["intervalo"])
                   for r in conn.execute(
                       "SELECT ticker_clave, campo, intervalo "
                       "FROM bloomberg_serie").fetchall()}
        nuevas = 0
        for s in lectura.pop("series"):
            registrar_serie(**s, conn=conn)
            if (clave_ticker(s["ticker"]), s["campo"], s["intervalo"]) not in previas:
                nuevas += 1
    lectura["nuevas"] = nuevas
    lectura["actualizadas"] = lectura["total"] - nuevas
    log(f"Registro: {nuevas} nuevas, {lectura['actualizadas']} actualizadas.")
    return lectura


def plantilla_series() -> bytes:
    """Example Excel with the right header and a few orienting rows."""
    ejemplo = [
        {"ticker": "SPX Index", "campo": "PX_LAST", "intervalo": "diario",
         "fecha_inicio": "2000-01-01", "descripcion": "S&P 500", "moneda": "USD"},
        {"ticker": "USDPEN Curncy", "campo": "PX_LAST", "intervalo": "diario",
         "fecha_inicio": "2010-01-01", "descripcion": "Tipo de cambio", "moneda": "PEN"},
        {"ticker": "PEGDPYOY Index", "campo": "PX_LAST", "intervalo": "trimestral",
         "fecha_inicio": "2005-01-01", "descripcion": "PBI Peru a/a", "moneda": ""},
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(ejemplo, columns=CABECERA_SERIES).to_excel(
            w, sheet_name="Series", index=False)
    return buf.getvalue()


def exportar_datos(serie_ids=None, desde=None, hasta=None) -> bytes:
    """
    The loaded data as Excel, one column per series. Wide on purpose:
    the table is long because that scales, but whoever takes a series
    to a spreadsheet wants it in columns.
    """
    df = leer(serie_ids, desde, hasta)
    reg = {s["serie_id"]: s for s in series_registro()}
    if df.empty:
        ancho = pd.DataFrame(columns=["fecha"])
    else:
        df["serie"] = [
            f"{reg[i]['ticker']} {reg[i]['campo']} ({reg[i]['intervalo']})"
            if i in reg else str(i) for i in df["serie_id"]
        ]
        ancho = (df.pivot_table(index="fecha", columns="serie", values="valor",
                                aggfunc="last")
                 .sort_index(ascending=False).reset_index())
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        ancho.to_excel(w, sheet_name="Bloomberg", index=False)
    return buf.getvalue()
