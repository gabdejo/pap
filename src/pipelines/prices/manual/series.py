# src/pipelines/prices/manual/series.py
# ---------------------------------------------------------------
# Manual price series: the store for benchmark components (or any
# auxiliary series) that NO vendor provides - the counterpart of the
# Bloomberg registry for data that is keyed in or uploaded.
#
# Tables serie_manual / serie_manual_dato (schemas 54/55). The
# benchmark composition references these with fuente='manual'; the
# levels themselves are always CALCULATED from the composition -
# what is loaded here is component prices, never benchmark levels.
#
# Point entry deletes explicitly (None removes that date). File
# loads NEVER delete: new dates insert, existing values are only
# replaced with refrescar=True, and an empty cell in the file is a
# row that never reaches the base.
#
# Canonical file format - one row per date, one value column:
#     fecha,valor
#     2026-08-20,128.4471
# Also accepted without configuring anything: separator , ; or tab,
# decimal comma or point, dd/mm/yyyy dates, the value column named
# valor / precio / price / nivel / indice / cierre / px_last, or a
# single unnamed second column.
# ---------------------------------------------------------------

import datetime as dt
import io
import logging

import pandas as pd

from src.db.connection import get_connection
from src.pipelines.prices.sbs.valor_cuota.benchmark_composicion import (motivo_en_uso, usos_de)
from src.shared import tabular

logger = logging.getLogger(__name__)

_COLS_FECHA = ("fecha", "date", "dia", "periodo")
_COLS_VALOR = ("valor", "precio", "price", "nivel", "indice", "cierre",
               "px_last", "close")

_INSERTA = """
    INSERT INTO serie_manual_dato (serie_id, fecha, valor)
    VALUES (%s, %s, %s)
    ON CONFLICT (serie_id, fecha) DO NOTHING
"""
_CORRIGE = """
    INSERT INTO serie_manual_dato (serie_id, fecha, valor)
    VALUES (%s, %s, %s)
    ON CONFLICT (serie_id, fecha) DO UPDATE
        SET valor = EXCLUDED.valor,
            actualizado_en = CURRENT_TIMESTAMP
"""


# ---- Registro (CRUD de series) --------------------------------------

def registrar_serie(nombre, descripcion=None, moneda=None) -> dict:
    """
    Registers or updates a series. Idempotent by nombre: resending
    updates the description instead of duplicating, and what is not
    sent is not erased.
    """
    nombre = str(nombre or "").strip()
    if not nombre:
        raise ValueError("La serie necesita un nombre.")
    descripcion = str(descripcion).strip() or None if descripcion else None
    moneda = str(moneda).strip().upper() or None if moneda else None
    with get_connection() as conn:
        fila = conn.execute(
            """
            INSERT INTO serie_manual (nombre, descripcion, moneda)
            VALUES (%s, %s, %s)
            ON CONFLICT (nombre) DO UPDATE
                SET descripcion = COALESCE(EXCLUDED.descripcion,
                                           serie_manual.descripcion),
                    moneda = COALESCE(EXCLUDED.moneda, serie_manual.moneda)
            RETURNING serie_id, nombre, descripcion, moneda
            """, (nombre, descripcion, moneda)).fetchone()
    return dict(fila)


def series() -> list[dict]:
    """The registry with its coverage and where each series is used."""
    with get_connection() as conn:
        filas = conn.execute(
            """
            SELECT s.serie_id, s.nombre, s.descripcion, s.moneda,
                   COUNT(d.fecha) AS puntos,
                   MIN(d.fecha) AS desde, MAX(d.fecha) AS hasta
            FROM serie_manual s
            LEFT JOIN serie_manual_dato d USING (serie_id)
            GROUP BY s.serie_id
            ORDER BY s.nombre
            """).fetchall()
        ultimos = conn.execute(
            """
            SELECT DISTINCT ON (serie_id) serie_id, valor
            FROM serie_manual_dato ORDER BY serie_id, fecha DESC
            """).fetchall()
        usos = conn.execute(
            """
            SELECT ref_id, COUNT(*) AS n FROM (
                SELECT ref_id FROM benchmark_composicion
                WHERE fuente = 'manual'
                UNION ALL
                SELECT fx_ref_id FROM benchmark_composicion
                WHERE fx_fuente = 'manual'
            ) u GROUP BY ref_id
            """).fetchall()
    ultimo = {u["serie_id"]: float(u["valor"]) for u in ultimos}
    en_uso = {u["ref_id"]: u["n"] for u in usos}
    return [{
        "serie_id": f["serie_id"], "nombre": f["nombre"],
        "descripcion": f["descripcion"], "moneda": f["moneda"],
        "puntos": int(f["puntos"]),
        "desde": str(f["desde"]) if f["desde"] else None,
        "hasta": str(f["hasta"]) if f["hasta"] else None,
        "ultimo": ultimo.get(f["serie_id"]),
        "usos": en_uso.get(f["serie_id"], 0),
    } for f in filas]


def borrar_serie(serie_id: int, con_datos: bool = False) -> dict:
    """
    Removes a series. Refuses while any benchmark composition still
    references it (breaking the recalc silently would be worse), and
    refuses to drop its data unless con_datos confirms it.
    """
    serie_id = int(serie_id)
    with get_connection() as conn:
        serie = _serie_o_error(conn, serie_id)
        vigentes, historicos = usos_de(conn, "manual", serie_id)
        if vigentes or historicos:
            raise ValueError(motivo_en_uso(serie["nombre"], vigentes, historicos))
        puntos = conn.execute(
            "SELECT COUNT(*) AS n FROM serie_manual_dato WHERE serie_id = %s",
            (serie_id,)).fetchone()["n"]
        if puntos and not con_datos:
            raise ValueError(
                f"'{serie['nombre']}' tiene {puntos} valor(es) cargados. "
                "Confirma que quieres borrar la serie con sus datos.")
        conn.execute("DELETE FROM serie_manual_dato WHERE serie_id = %s",
                     (serie_id,))
        conn.execute("DELETE FROM serie_manual WHERE serie_id = %s",
                     (serie_id,))
    logger.info(f"serie manual borrada: {serie['nombre']} ({puntos} puntos).")
    return {"serie_id": serie_id, "nombre": serie["nombre"], "puntos": puntos}


def _serie_o_error(conn, serie_id: int) -> dict:
    fila = conn.execute(
        "SELECT serie_id, nombre FROM serie_manual WHERE serie_id = %s",
        (int(serie_id),)).fetchone()
    if fila is None:
        raise ValueError(f"No existe la serie manual con id {serie_id}.")
    return dict(fila)


# ---- Datos -----------------------------------------------------------

def leer(serie_id: int, desde=None, hasta=None) -> list[dict]:
    """Points of one series, ascending by date."""
    sql = "SELECT fecha, valor FROM serie_manual_dato WHERE serie_id = %s"
    params: list = [int(serie_id)]
    if desde:
        sql += " AND fecha >= %s::date"
        params.append(str(pd.to_datetime(desde).date()))
    if hasta:
        sql += " AND fecha <= %s::date"
        params.append(str(pd.to_datetime(hasta).date()))
    sql += " ORDER BY fecha"
    with get_connection() as conn:
        filas = conn.execute(sql, params).fetchall()
    return [{"fecha": str(f["fecha"]), "valor": float(f["valor"])}
            for f in filas]


def registrar_valores(serie_id: int, valores: dict) -> dict:
    """
    Point entry: {fecha: valor|None}. A number writes (replacing what
    was there), None DELETES that date - same grammar as the valor
    cuota form. Dates cannot be future; values must be positive
    (these are prices, index levels or FX - zero or negative would
    poison the benchmark chaining).
    """
    if not valores:
        raise ValueError("No llego ningun valor.")
    limpios: dict[dt.date, float | None] = {}
    for f, v in valores.items():
        fecha = pd.to_datetime(f).date()
        if fecha > dt.date.today():
            raise ValueError(f"La fecha {fecha} es futura.")
        if v is None or str(v).strip() == "":
            limpios[fecha] = None
            continue
        try:
            num = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"Valor ilegible para {fecha}: {v!r}.")
        if num != num or num <= 0:
            raise ValueError(f"El valor de {fecha} debe ser mayor que cero.")
        limpios[fecha] = num

    escritos = borrados = 0
    with get_connection() as conn:
        serie = _serie_o_error(conn, serie_id)
        for fecha, num in limpios.items():
            if num is None:
                cur = conn.execute(
                    "DELETE FROM serie_manual_dato "
                    "WHERE serie_id = %s AND fecha = %s",
                    (serie["serie_id"], fecha))
                borrados += cur.rowcount
            else:
                conn.execute(_CORRIGE, (serie["serie_id"], fecha, num))
                escritos += 1
    logger.info(f"serie manual '{serie['nombre']}': {escritos} escritos, "
                f"{borrados} borrados.")
    return {"serie_id": serie["serie_id"], "nombre": serie["nombre"],
            "escritos": escritos, "borrados": borrados}


# ---- Carga por archivo ----------------------------------------------

def leer_archivo_valores(datos, hoja=None) -> dict:
    """
    Excel or CSV -> {fecha: valor}, without touching the DB. Returns
    what was read plus everything deduced and discarded, so it can be
    shown before saving. Format is recognized by content, not
    extension.
    """
    if tabular.es_excel(datos):
        filas, nombre_hoja, hojas = tabular.filas_de_excel(datos, hoja)
        origen, sep = "excel", None
        # Excel numbers arrive typed; text-formatted cells are still
        # ambiguous, so the decimal style is deduced from the sheet.
        import re
        textos = [str(c).strip() for f in filas for c in f
                  if isinstance(c, str) and re.fullmatch(r"-?[\d.,\s]+", c.strip())
                  and re.search(r"\d", c)]
        coma_decimal = tabular.estilo_decimal(textos)
        # Undecidable style with separator-bearing TEXT cells is refused,
        # not guessed (see leer_archivo_benchmark's history: a wrong
        # guess is a silent 1000x inflation).
        if coma_decimal is None and any("," in t or "." in t for t in textos):
            raise ValueError(
                "No se pudo decidir si la coma o el punto es el separador "
                "decimal del archivo (las celdas de texto son ambiguas). "
                "Da formato numerico a las celdas en Excel, o exporta a "
                "CSV, y vuelve a subirlo.")
    else:
        filas, sep, coma_decimal = tabular.filas_de_csv(datos)
        origen, nombre_hoja, hojas = "csv", None, []

    avisos, omitidas = [], []
    icab = tabular.fila_cabecera(
        filas, lambda c: tabular.clave_col(c) in _COLS_FECHA)
    if icab is None:
        vistas = [str(c) for f in filas[:3] for c in f if str(c).strip()
                  and str(c) != "nan"]
        raise ValueError(
            "Falta la columna 'fecha'. No se encontro una fila de encabezados "
            f"en las primeras filas del archivo. Se leyo: "
            f"{', '.join(vistas[:10]) or 'nada'}")
    if icab:
        avisos.append(f"Los encabezados estaban en la fila {icab + 1}; "
                      "lo anterior se ignoro.")

    cabecera = tabular.limpiar_cabecera(filas[icab])
    claves = [tabular.clave_col(c) for c in cabecera]
    icol_fecha = next(i for i, c in enumerate(claves) if c in _COLS_FECHA)
    icol_valor = next((i for i, c in enumerate(claves) if c in _COLS_VALOR),
                      None)
    if icol_valor is None:
        # A single other named column is unambiguous enough; more than
        # one is a guess this reader refuses to make.
        otras = [i for i, c in enumerate(cabecera)
                 if i != icol_fecha and str(c).strip()]
        if len(otras) == 1:
            icol_valor = otras[0]
            avisos.append(f"Se tomo la columna '{cabecera[icol_valor]}' "
                          "como el valor.")
        else:
            raise ValueError(
                "No se encontro la columna del valor. Nombra una columna "
                "'valor' (o precio / nivel / indice), o deja solo dos "
                "columnas: fecha y valor. La cabecera leida fue: "
                + ", ".join(c for c in cabecera if c))

    hoy = dt.date.today()
    por_fecha: dict[dt.date, float] = {}
    repetidas = 0
    for n, fila in enumerate(filas[icab + 1:], start=icab + 2):
        def vacia(v):
            return (v is None or (isinstance(v, float) and v != v)
                    or str(v).strip() == "")
        if all(vacia(x) for x in fila):
            continue
        if len(fila) <= icol_fecha:
            omitidas.append({"linea": n, "motivo": "fila incompleta"})
            continue
        f = tabular.fecha_flexible(fila[icol_fecha])
        if f is None:
            omitidas.append({"linea": n,
                             "motivo": f"fecha ilegible: {str(fila[icol_fecha])[:24]}"})
            continue
        if f > hoy:
            omitidas.append({"linea": n, "motivo": f"fecha futura: {f}"})
            continue
        crudo = fila[icol_valor] if len(fila) > icol_valor else None
        v = tabular.num_flexible(crudo, coma_decimal)
        if v is None:
            continue
        if v <= 0:
            omitidas.append({"linea": n, "motivo": f"no es positivo: {v}"})
            continue
        if f in por_fecha:
            repetidas += 1
        por_fecha[f] = v

    if repetidas:
        avisos.append(f"{repetidas} fecha(s) venian repetidas; manda la "
                      "ultima lectura.")
    if not por_fecha:
        raise ValueError("No se pudo leer ningun valor valido. Revisa el "
                         "formato: se esperaba 'fecha' y 'valor'.")

    fechas = sorted(por_fecha)
    salida = {"valores": por_fecha,
              "muestra": _muestra(por_fecha),
              "origen": origen,
              "filas": len(por_fecha),
              "desde": str(fechas[0]), "hasta": str(fechas[-1]),
              "omitidas": omitidas[:25], "total_omitidas": len(omitidas),
              "avisos": avisos}
    if origen == "excel":
        salida["hoja"] = nombre_hoja
        salida["hojas"] = hojas
    else:
        salida["separador"] = {"\t": "tabulacion"}.get(sep, sep)
        salida["decimal"] = "coma" if coma_decimal else "punto"
    return salida


def _muestra(por_fecha: dict, filas: int = 15) -> dict:
    """Last rows read, most recent first - the visual half of the review."""
    ultimas = sorted(por_fecha, reverse=True)[:filas]
    return {"columnas": ["Valor"],
            "filas": [[str(f), por_fecha[f]] for f in ultimas],
            "total": len(por_fecha)}


def guardar_valores(serie_id: int, por_fecha: dict,
                    refrescar: bool = False) -> dict:
    """
    Writes a reviewed file without ever deleting. New dates insert;
    existing values are only replaced with refrescar=True. Counters
    are honest: they compare against what is stored, and an identical
    value is skipped entirely (no write, no count).
    """
    if not por_fecha:
        return {"nuevas": 0, "actualizadas": 0, "sin_cambio": 0}
    with get_connection() as conn:
        serie = _serie_o_error(conn, serie_id)
        fechas = sorted(por_fecha)
        existentes = {
            r["fecha"]: float(r["valor"])
            for r in conn.execute(
                "SELECT fecha, valor FROM serie_manual_dato "
                "WHERE serie_id = %s AND fecha BETWEEN %s AND %s",
                (serie["serie_id"], fechas[0], fechas[-1])).fetchall()}
        nuevas = actualizadas = sin_cambio = 0
        for fecha, v in por_fecha.items():
            previo = existentes.get(fecha)
            if previo is None:
                conn.execute(_INSERTA, (serie["serie_id"], fecha, v))
                nuevas += 1
            elif refrescar and abs(previo - v) > 1e-9:
                conn.execute(_CORRIGE, (serie["serie_id"], fecha, v))
                actualizadas += 1
            else:
                sin_cambio += 1
    res = {"nuevas": nuevas, "actualizadas": actualizadas,
           "sin_cambio": sin_cambio}
    logger.info(f"serie manual '{serie['nombre']}': {nuevas} fechas nuevas, "
                f"{actualizadas} actualizadas, {sin_cambio} sin cambio.")
    return res


def importar_valores(serie_id: int, datos, refrescar: bool = False,
                     hoja=None) -> dict:
    """Reads the file and saves it. Returns both halves' detail."""
    lectura = leer_archivo_valores(datos, hoja=hoja)
    guardado = guardar_valores(serie_id, lectura.pop("valores"),
                               refrescar=refrescar)
    lectura.update(guardado)
    lectura["refrescar"] = bool(refrescar)
    return lectura


# ---- Plantilla y export ---------------------------------------------

def plantilla(filas: int = 10) -> bytes:
    """Empty Excel with the right header and the last business days."""
    dias = list(pd.bdate_range(end=dt.date.today(), periods=max(1, filas)).date)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame({"fecha": dias, "valor": [None] * len(dias)}).to_excel(
            w, sheet_name="Serie", index=False)
    return buf.getvalue()


def exportar_datos(serie_id: int, desde=None, hasta=None) -> bytes:
    """
    One series as an in-memory Excel, in the SAME format it imports:
    what you download can be re-uploaded untouched.
    """
    puntos = leer(serie_id, desde, hasta)
    df = pd.DataFrame(puntos or [], columns=["fecha", "valor"])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.sort_values("fecha", ascending=False).to_excel(
            w, sheet_name="Serie", index=False)
    return buf.getvalue()
