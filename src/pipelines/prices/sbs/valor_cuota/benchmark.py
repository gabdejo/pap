# src/pipelines/prices/sbs/valor_cuota/benchmark.py
# ---------------------------------------------------------------
# Composite indices per fund type: READ side only.
#
# Two indices per fund, told apart by tipo ('target' / 'benchmark'),
# common to all AFPs, only for the funds each one is declared for
# (Fund 0 is capital-protected and has no market comparable). Their
# levels live as series SPP_TARGET_F{n} / SPP_BENCH_F{n}, PX_LAST,
# source 'benchmark' in fact_prices, and they are written by exactly
# ONE hand: the recalculation in benchmark_composicion.py
# (composition of priced components -> chained index). Levels are
# calculated, never keyed in; what IS keyed in is component prices,
# in src/pipelines/prices/manual/series.py.
#
# The module keeps the name it had when there was one index. What it
# offers is the reading: the wide frame the tablero charts, the
# coverage summary, and the Excel export of the stored series - each
# for one tipo at a time.
# ---------------------------------------------------------------

import io
import logging

import pandas as pd

from src.db.connection import get_connection
from src.pipelines.prices.sbs.valor_cuota import afps as reg

logger = logging.getLogger(__name__)


def columna_indice(tipo: str, fondo: int) -> str:
    return f"{reg.tipo_indice(tipo)}_f{int(fondo)}"


# ---- Lectura --------------------------------------------------------

def leer_indice(tipo: str, desde=None, hasta=None) -> pd.DataFrame:
    """Wide DataFrame: fecha x {tipo}_f{n}, ascending by date."""
    tipo = reg.tipo_indice(tipo)
    prefijo = reg.prefijo_indice(tipo).replace("_", r"\_") + r"\_F%"
    sql = """
        SELECT e.procode, fp.date, fp.price
        FROM fact_prices fp
        JOIN series_registry sr ON sr.series_id = fp.series_id
        JOIN dim_entity e ON e.entity_id = sr.entity_id
        WHERE e.procode LIKE %s AND sr.source = %s
    """
    params: list = [prefijo, reg.SOURCE_BENCH]
    if desde:
        sql += " AND fp.date >= %s::date"
        params.append(str(pd.to_datetime(desde).date()))
    if hasta:
        sql += " AND fp.date <= %s::date"
        params.append(str(pd.to_datetime(hasta).date()))
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    if not rows:
        return pd.DataFrame(columns=["fecha"])
    df = pd.DataFrame([
        {"fecha": r["date"],
         "col": columna_indice(tipo, int(r["procode"].rsplit("_F", 1)[1])),
         "valor": r["price"]}
        for r in rows
    ])
    df = (df.pivot_table(index="fecha", columns="col", values="valor",
                         aggfunc="last")
          .sort_index().reset_index())
    df.columns.name = None
    return df


def estado_indice(tipo: str) -> dict:
    """Coverage summary, in the same language as the rest of the tablero."""
    tipo = reg.tipo_indice(tipo)
    df = leer_indice(tipo)
    fondos_i = reg.fondos_indice(tipo)
    base = {"tipo": tipo, "tabla": "fact_prices", "fondos": fondos_i,
            "filas": len(df), "desde": None, "hasta": None, "series": []}
    if df.empty:
        return base
    base.update(desde=str(df["fecha"].min()), hasta=str(df["fecha"].max()))
    for f in fondos_i:
        col = columna_indice(tipo, f)
        if col not in df.columns:
            base["series"].append({"fondo": f, "puntos": 0, "valor": None,
                                   "fecha": None, "inicio": None, "var_bps": None})
            continue
        s = df[["fecha", col]].dropna(subset=[col])
        if s.empty:
            base["series"].append({"fondo": f, "puntos": 0, "valor": None,
                                   "fecha": None, "inicio": None, "var_bps": None})
            continue
        ultimo = float(s[col].iloc[-1])
        previo = float(s[col].iloc[-2]) if len(s) > 1 else None
        base["series"].append({
            "fondo": f, "puntos": int(len(s)), "valor": ultimo,
            "fecha": str(s["fecha"].iloc[-1]), "inicio": str(s["fecha"].iloc[0]),
            "var_bps": None if not previo else round((ultimo / previo - 1) * 10000, 1)})
    return base


# ---- Export ---------------------------------------------------------

def exportar_indice_datos(tipo: str, desde=None, hasta=None) -> bytes:
    """The calculated index as an in-memory Excel, one column per
    fund, most recent first."""
    tipo = reg.tipo_indice(tipo)
    df = leer_indice(tipo, desde, hasta)
    fondos_i = reg.fondos_indice(tipo)
    cols = [columna_indice(tipo, f) for f in fondos_i if columna_indice(tipo, f) in df.columns]
    if df.empty:
        df = pd.DataFrame(columns=["fecha"] + [columna_indice(tipo, f) for f in fondos_i])
        cols = [columna_indice(tipo, f) for f in fondos_i]
    df = df[["fecha"] + cols].rename(
        columns={columna_indice(tipo, f): f"fondo{f}" for f in fondos_i})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.sort_values("fecha", ascending=False).to_excel(
            w, sheet_name=reg.ETIQUETA_INDICE[tipo], index=False)
    return buf.getvalue()
