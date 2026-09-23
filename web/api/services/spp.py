
# web/api/services/spp.py
# ---------------------------------------------------------------------------
# SPP valor cuota service: everything the tablero reads and nothing that
# writes. Ported from the standalone monitor's read paths (sbs.py estado /
# ventanas / posiciones / tabla) onto the dimensional model.
#
# The monitor stored the book as one wide table; here it lives long in
# fact_prices. leer() rebuilds the wide frame (fecha x '{clave}_f{n}[sufijo]')
# by pivoting, so the ported window/ranking logic runs unchanged - those
# calculations are share-grid by nature (one common closing-date grid for all
# AFPs, or relative performance would compare different periods).
# ---------------------------------------------------------------------------
from __future__ import annotations

import datetime as dt
import io
import logging

import numpy as np
import pandas as pd

from src.db.connection import get_connection
from src.pipelines.prices.sbs.valor_cuota import afps as reg

logger = logging.getLogger(__name__)

SUFIJO = {"valor_cuota": "", "cuotas": "_cuotas", "fondo": "_fondo"}
METRICAS = list(SUFIJO)

# Lo que el selector del tablero OFRECE, que no es lo mismo que lo que se
# guarda. 'cuotas' se sigue extrayendo, cargando y sirviendo - la tabla de
# cierres la muestra y la API la acepta por su nombre - pero como serie que
# graficar no aporta: son unidades administrativas, no un precio, y su
# rendimiento no significa nada. Sacarla de METRICAS en cambio la habria
# borrado del dato: esa lista manda tambien en que columnas se leen.
METRICAS_TABLERO = ["valor_cuota", "fondo"]

MESES_CORTOS = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
                "JUL", "AGO", "SET", "OCT", "NOV", "DIC"]

# Window keys and labels. Column order is CHRONOLOGICAL, oldest on the
# left and the control date on the right, so the tables read in the same
# direction as the line chart and the positions heatmap (the monitor had
# them recent-first). Mixed windows are ordered by their START date:
# last year < YTD < M-2 < M-1 < MTD < 5D < the daily moves < the level.
CLAVES_NIVEL = ["anio1", "anio", "mes2", "mes1", "mes",
                "t5", "t4", "t3", "t2", "t1", "t"]
CLAVES_REND = [
    ("a1",  None,          "pct"),
    # El ejercicio arranca el 31/10, o sea ANTES del 1/1, salvo en
    # noviembre y diciembre, cuando el ejercicio ya empezo y el año no.
    # La columna no se mueve de sitio esos dos meses: reordenarla dos
    # veces al año costaria mas de lo que aclara.
    ("fy",  "FY",          "pct"),
    ("ytd", "YTD",         "pct"),
    ("m2",  None,          "bps"),
    ("m1",  None,          "bps"),
    ("mtd", "MTD",         "bps"),
    ("d5",  "5D",          "bps"),
    ("d3",  None,          "bps"),
    ("d2",  None,          "bps"),
    ("d1",  None,          "bps"),
    ("d0",  None,          "bps"),
    ("vc",  "Valor cuota", "nivel"),
]


def columna(afp, fondo: int, metrica: str = "valor_cuota") -> str:
    """'{clave}_f{n}[sufijo]' - the monitor's stable column identity."""
    clave = reg.clave_de(afp)
    if not clave:
        raise ValueError(f"AFP no registrada: {afp}")
    return f"{clave}_f{int(fondo)}{SUFIJO[metrica]}"


def _metrica_valida(metrica) -> str:
    m = str(metrica or "valor_cuota").strip()
    return m if m in SUFIJO else "valor_cuota"


def _metricas_pedidas(metrica: str) -> list[str]:
    """'todas' -> the three metrics, anything else -> its valid one."""
    return list(METRICAS) if metrica == "todas" else [_metrica_valida(metrica)]


def _fecha_control(fechas: list, fecha) -> dt.date | None:
    """Snaps the requested control date to the last close <= it (the
    common rule of ventanas() and posiciones()); None when nothing
    precedes it."""
    control = pd.to_datetime(fecha).date() if fecha else fechas[-1]
    if control in fechas:
        return control
    anteriores = [f for f in fechas if f <= control]
    return anteriores[-1] if anteriores else None


def _valor(porfecha: pd.DataFrame, col: str, f):
    """One cell of the wide book, None-safe (shared lookup of
    ventanas() and posiciones())."""
    if f is None or col not in porfecha.columns:
        return None
    try:
        v = porfecha.at[f, col]
    except KeyError:
        return None
    return None if pd.isna(v) else float(v)


def _ultima_fecha():
    """Cheap MAX(date) probe so heavy readers can bound leer() relative
    to the book's real end instead of fetching 30 years to use two."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT MAX(fp.date) AS ultima
            FROM fact_prices fp
            JOIN series_registry sr ON sr.series_id = fp.series_id
            JOIN dim_entity e ON e.entity_id = sr.entity_id
            WHERE e.procode LIKE 'SPP\\_%%' AND sr.source = %s
            """, (reg.SOURCE_SBS,)).fetchone()
    return row["ultima"] if row else None


# ---- Book reader ----------------------------------------------------------

def leer(metricas: list[str] | None = None,
         desde=None, hasta=None) -> pd.DataFrame:
    """
    Wide DataFrame: one row per fecha, one column per AFP x fund x metric,
    named '{clave}_f{n}[sufijo]'. Only the requested metrics are pivoted -
    estado() needs all three, a chart request needs one.
    """
    metricas = [m for m in (metricas or METRICAS) if m in SUFIJO] or METRICAS
    fields = [reg.METRICA_FIELD[m] for m in metricas]

    sql = """
        SELECT e.procode, sr.field, fp.date, fp.price
        FROM fact_prices fp
        JOIN series_registry sr ON sr.series_id = fp.series_id
        JOIN dim_entity e ON e.entity_id = sr.entity_id
        WHERE e.procode LIKE 'SPP\\_%%'
          AND sr.source = %s
          AND sr.field = ANY(%s)
    """
    params: list = [reg.SOURCE_SBS, fields]
    if desde:
        sql += " AND fp.date >= %s::date"
        params.append(str(desde))
    if hasta:
        sql += " AND fp.date <= %s::date"
        params.append(str(hasta))

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    if not rows:
        return pd.DataFrame(columns=["fecha"])

    registros = []
    for r in rows:
        parsed = reg.parse_procode(r["procode"])
        if parsed is None:
            continue
        clave, fondo = parsed
        metrica = reg.FIELD_METRICA.get(r["field"])
        if metrica is None:
            continue
        registros.append({"fecha": r["date"],
                          "col": f"{clave}_f{fondo}{SUFIJO[metrica]}",
                          "valor": r["price"]})
    if not registros:
        return pd.DataFrame(columns=["fecha"])

    df = (pd.DataFrame(registros)
          .pivot_table(index="fecha", columns="col", values="valor", aggfunc="last")
          .sort_index().reset_index())
    df.columns.name = None
    return df


# ---- Config ---------------------------------------------------------------

def config() -> dict:
    """The AFP registry the interface consumes - it never names an AFP."""
    return {
        "fondos": reg.fondos(),
        # Dos indices compuestos por fondo, con los fondos que lleva cada uno
        # y como se llaman. El tablero no escribe "Target" ni "Benchmark"
        # por su cuenta: lo lee de aqui.
        "tipos_indice": [{"clave": t, "etiqueta": reg.ETIQUETA_INDICE[t]}
                         for t in reg.TIPOS_INDICE],
        "fondos_indice": {t: reg.fondos_indice(t) for t in reg.TIPOS_INDICE},
        "casa": reg.afp_casa(),
        "metricas": [{"clave": k, "etiqueta": reg.ETIQUETA_METRICA[k]}
                     for k in METRICAS_TABLERO],
        "afps": [{"clave": a["clave"],
                  "nombre": a["nombre"],
                  "color": a.get("color", "#1A1A1A"),
                  "color_solido": a.get("color_solido", a.get("color", "#1A1A1A")),
                  "casa": bool(a.get("casa")),
                  "fondos": reg.fondos_de(a["nombre"])}
                 for a in reg.afps()],
    }


# ---- Estado ---------------------------------------------------------------

def huecos(fechas, minimo: int = 3) -> list:
    """
    Consecutive business days without data inside the loaded range. The
    source has no holiday calendar, so Peruvian holidays show as empty
    business days; typical blocks span 2, so only runs of `minimo`+ are
    real missing information.
    """
    fechas = list(fechas)
    if len(fechas) < 2:
        return []
    cargadas = {pd.to_datetime(f).date() for f in fechas}
    faltan = sorted(set(pd.bdate_range(min(cargadas), max(cargadas)).date) - cargadas)
    if not faltan:
        return []
    tramos, actual = [], [faltan[0]]
    for previo, siguiente in zip(faltan, faltan[1:]):
        if int(np.busday_count(previo + dt.timedelta(days=1), siguiente)) == 0:
            actual.append(siguiente)
        else:
            tramos.append(actual)
            actual = [siguiente]
    tramos.append(actual)
    return [{"desde": str(t[0]), "hasta": str(t[-1]), "dias": len(t)}
            for t in tramos if len(t) >= minimo]


def _cobertura_metrica(df: pd.DataFrame, metrica: str) -> dict:
    cols = [c for c in _columnas_de(metrica) if c in df.columns]
    if not cols or df.empty:
        return {"metrica": metrica, "fechas": 0, "desde": None, "hasta": None}
    con_dato = df[df[cols].notna().any(axis=1)]
    if con_dato.empty:
        return {"metrica": metrica, "fechas": 0, "desde": None, "hasta": None}
    return {"metrica": metrica, "fechas": int(len(con_dato)),
            "desde": str(con_dato["fecha"].min()),
            "hasta": str(con_dato["fecha"].max())}


def _columnas_de(metrica: str) -> list[str]:
    return [columna(a, f, metrica)
            for f in reg.fondos() for a in reg.nombres() if reg.opera(a, f)]


def estado() -> dict:
    """Summary for the tablero: coverage, latest values, gaps, variations."""
    df = leer()
    base = {"tabla": "fact_prices", "filas": len(df), "desde": None,
            "hasta": None, "huecos": [], "series": [], "completas": 0,
            "parciales": 0,
            "metricas": [_cobertura_metrica(df, m) for m in METRICAS]}
    if df.empty:
        return base

    base.update(desde=str(df["fecha"].min()), hasta=str(df["fecha"].max()),
                huecos=huecos(df["fecha"]))

    # A "complete" date has all valor cuota series published; usable
    # coverage runs to the last of those - a half-published date is not
    # coverage.
    presentes = [c for c in _columnas_de("valor_cuota") if c in df.columns]
    completas = df[presentes].notna().all(axis=1) if presentes else df["fecha"].isna()
    base["completas"] = int(completas.sum())
    base["parciales"] = int((~completas).sum())
    fechas_ok = df.loc[completas, "fecha"]
    base["desde_completa"] = str(fechas_ok.min()) if len(fechas_ok) else None
    base["hasta_completa"] = str(fechas_ok.max()) if len(fechas_ok) else None
    base["posteriores_parciales"] = (
        int((df["fecha"] > fechas_ok.max()).sum()) if len(fechas_ok) else 0)

    # Staleness KPI, owned here next to the coverage semantics it depends
    # on (the dashboard only renders tone): business days elapsed since
    # the last COMPLETE close, and the data-quality classification.
    fin = fechas_ok.max() if len(fechas_ok) else df["fecha"].max()
    hoy = dt.date.today()
    rezago = int(np.busday_count(fin + dt.timedelta(days=1),
                                 hoy + dt.timedelta(days=1))) if fin < hoy else 0
    abiertos = len(base["huecos"])
    if rezago > 3 or abiertos > 1:
        integridad = "Excedido"
    elif abiertos == 1 or rezago > 1:
        integridad = "Observación"
    else:
        integridad = "Dentro"
    base["rezago_habiles"] = max(rezago, 0)
    base["integridad"] = integridad

    def ultimo_y_var(col):
        if col not in df.columns:
            return None, None, None
        s = df[["fecha", col]].dropna(subset=[col])
        if s.empty:
            return None, None, None
        ultimo = float(s[col].iloc[-1])
        previo = float(s[col].iloc[-2]) if len(s) > 1 else None
        var = None if not previo else round((ultimo / previo - 1) * 10000, 1)
        return ultimo, var, str(s["fecha"].iloc[-1])

    for f in reg.fondos():
        for a in reg.nombres():
            if not reg.opera(a, f):
                continue
            col = columna(a, f)
            if col not in df.columns:
                continue
            serie = df[["fecha", col]].dropna(subset=[col])
            if serie.empty:
                continue
            vc, vc_var, vc_fecha = ultimo_y_var(col)
            cu, cu_var, cu_fecha = ultimo_y_var(columna(a, f, "cuotas"))
            fo, fo_var, fo_fecha = ultimo_y_var(columna(a, f, "fondo"))
            base["series"].append({
                "afp": a, "fondo": f, "columna": col,
                "fecha": vc_fecha, "valor": vc, "var_bps": vc_var,
                "cuotas": cu, "cuotas_var_bps": cu_var, "cuotas_fecha": cu_fecha,
                "fondo_soles": fo, "fondo_var_bps": fo_var, "fondo_fecha": fo_fecha,
                "puntos": int(len(serie)),
                "inicio": str(serie["fecha"].iloc[0]),
            })
    return base


# ---- Serie (chart) --------------------------------------------------------

def serie(fondo: int, afps_pedidas: list[str] | None, metrica: str,
          desde=None, hasta=None) -> dict:
    metrica = _metrica_valida(metrica)
    fondo = int(fondo) if int(fondo) in reg.fondos() else reg.fondos()[0]
    nombres = reg.nombres()
    pedidas = [a for a in (afps_pedidas or []) if a in nombres] or nombres

    df = leer([metrica], desde, hasta)
    if df.empty:
        return {"fondo": fondo, "metrica": metrica, "series": []}

    salida = []
    for afp in pedidas:
        col = columna(afp, fondo, metrica)
        if col not in df.columns:
            salida.append({"afp": afp, "puntos": []})
            continue
        s = df[["fecha", col]].dropna(subset=[col])
        salida.append({
            "afp": afp,
            "puntos": [[str(f), float(v)] for f, v in zip(s["fecha"], s[col])],
        })
    return {"fondo": fondo, "metrica": metrica, "series": salida}


# ---- Ventanas de rendimiento ----------------------------------------------

def _dm(f):
    return f.strftime("%d/%m") if f else "—"


def _dmy(f):
    return f.strftime("%d/%m/%y") if f else "—"


def _mes_corto(f):
    return f"{MESES_CORTOS[f.month - 1]} {f.strftime('%y')}" if f else "—"


def _primer_dia_mes(d: dt.date, retroceso: int = 0) -> dt.date:
    y, m = d.year, d.month - retroceso
    while m <= 0:
        m += 12
        y -= 1
    return dt.date(y, m, 1)


def _inicio_fy(d: dt.date) -> dt.date:
    """
    Day the fiscal year containing `d` starts. The year runs 31/10 to
    31/10.

    The boundary date belongs to the year that ENDS on it: standing on
    31/10 the operator is reading the twelve months just closed, not a
    year that is one day old. So the new year starts on 1/11, and the
    close of 31/10 is its base - which is what "desde el 31/10" means.
    """
    anio = d.year if d > dt.date(d.year, 10, 31) else d.year - 1
    return dt.date(anio, 11, 1)


def _ultimo_antes(fechas: list, limite: dt.date):
    previos = [f for f in fechas if f < limite]
    return previos[-1] if previos else None


def fechas_referencia(fechas: list, control: dt.date) -> dict:
    """
    Base dates of every window on the COMMON closing grid - one grid for
    all series, or relative returns would compare different periods.
    Month/year bases are the LAST CLOSE OF THE PRIOR PERIOD (the desk
    standard), so MTD includes the first business day.
    """
    previos = [f for f in fechas if f < control]
    ref = {"t": control}
    for i in range(1, 6):
        ref[f"t{i}"] = previos[-i] if len(previos) >= i else None
    ref["mes"] = _ultimo_antes(fechas, _primer_dia_mes(control, 0))
    ref["mes1"] = _ultimo_antes(fechas, _primer_dia_mes(control, 1))
    ref["mes2"] = _ultimo_antes(fechas, _primer_dia_mes(control, 2))
    ref["anio"] = _ultimo_antes(fechas, dt.date(control.year, 1, 1))
    ref["anio1"] = _ultimo_antes(fechas, dt.date(control.year - 1, 1, 1))
    # El ejercicio: misma regla que el mes y el año - la base es el ultimo
    # cierre del periodo anterior, o sea el del 31/10 (o el ultimo dia de
    # cotizacion de octubre, si el 31 cayo en fin de semana o feriado).
    ref["fy"] = _ultimo_antes(fechas, _inicio_fy(control))
    return ref


def _variacion(actual, base):
    if actual is None or base is None or base == 0:
        return None
    return actual / base - 1.0


def ventanas(fecha=None, metrica: str = "valor_cuota") -> dict:
    """
    Levels, absolute returns and relative returns (house minus each
    competitor) per AFP and fund, on the common closing grid.
    """
    metrica = _metrica_valida(metrica)
    vacio = {"control": None, "fechas": {}, "niveles": [], "absolutos": [],
             "relativos": [], "cols_nivel": [], "cols_rend": []}
    # Every window base (t-5, month starts, 31/10 of the fiscal year, Jan 1
    # of the prior year) lives within two calendar years of the control
    # date: bound the read there
    # instead of pulling the book since 1993 on every request.
    tope = _ultima_fecha()
    if tope is None:
        return vacio
    ancla = min(pd.to_datetime(fecha).date(), tope) if fecha else tope
    df = leer([metrica], desde=dt.date(ancla.year - 2, 1, 1))
    if df.empty:
        return vacio

    fechas = sorted(df["fecha"].tolist())
    control = _fecha_control(fechas, fecha)
    if control is None:
        return vacio

    ref = fechas_referencia(fechas, control)
    porfecha = df.set_index("fecha")

    def valor(col, f):
        return _valor(porfecha, col, f)

    nombres = reg.nombres()
    niveles, absolutos, rend = [], [], {}

    for f in reg.fondos():
        for a in nombres:
            if not reg.opera(a, f):
                continue
            col = columna(a, f, metrica)
            v = {k: valor(col, ref.get(k)) for k in ref}

            niveles.append({"afp": a, "fondo": f,
                            "valores": {k: v.get(k) for k in CLAVES_NIVEL}})

            r = {
                "vc":  v["t"],
                "d0":  _variacion(v["t"],  v["t1"]),
                "d1":  _variacion(v["t1"], v["t2"]),
                "d2":  _variacion(v["t2"], v["t3"]),
                "d3":  _variacion(v["t3"], v["t4"]),
                "d5":  _variacion(v["t"],  v["t5"]),
                "mtd": _variacion(v["t"],  v["mes"]),
                "m1":  _variacion(v["mes"],  v["mes1"]),
                "m2":  _variacion(v["mes1"], v["mes2"]),
                "fy":  _variacion(v["t"],    v["fy"]),
                "ytd": _variacion(v["t"],    v["anio"]),
                "a1":  _variacion(v["anio"], v["anio1"]),
            }
            rend[(a, f)] = r
            absolutos.append({"afp": a, "fondo": f, "valores": r})

    # Relative: the house appears too, but with its ABSOLUTE return - the
    # baseline the differences are read against.
    claves = [k for k, _, u in CLAVES_REND if u != "nivel"]
    casa = reg.afp_casa()
    relativos = []
    for f in reg.fondos():
        if (casa, f) not in rend:
            continue
        base = rend[(casa, f)]
        relativos.append({"afp": casa, "fondo": f, "absoluto": True,
                          "valores": {k: base.get(k) for k in claves}})
        for a in nombres:
            if a == casa or (a, f) not in rend:
                continue
            otra = rend[(a, f)]
            dif = {k: (None if (base.get(k) is None or otra.get(k) is None)
                       else base[k] - otra[k]) for k in claves}
            relativos.append({"afp": a, "fondo": f, "absoluto": False,
                              "valores": dif})

    cols_nivel = [[k, (_dmy(ref[k]) if k in ("anio", "anio1") else _dm(ref[k]))]
                  for k in CLAVES_NIVEL]
    rotulo_rend = {
        "d0": _dm(ref["t"]), "d1": _dm(ref["t1"]),
        "d2": _dm(ref["t2"]), "d3": _dm(ref["t3"]),
        "m1": _mes_corto(ref["mes"]),
        "m2": _mes_corto(ref["mes1"]),
        "a1": ref["anio"].strftime("%Y") if ref["anio"] else "—",
    }
    cols_rend = [[k, (fija or rotulo_rend.get(k, k)), u]
                 for k, fija, u in CLAVES_REND]

    return {"control": str(control),
            "fechas": {k: (str(x) if x else None) for k, x in ref.items()},
            "metrica": metrica,
            "niveles": niveles, "absolutos": absolutos, "relativos": relativos,
            "cols_nivel": cols_nivel, "cols_rend": cols_rend}


# ---- Posiciones mensuales -------------------------------------------------

def posiciones(fecha=None, meses: int = 24, metrica: str = "valor_cuota") -> dict:
    """
    Monthly performance rank per AFP, grouped by FUND TYPE - ranking only
    makes sense between portfolios of the same risk profile. Each closed
    month returns close-vs-prior-close; the last period is the MTD.
    """
    metrica = _metrica_valida(metrica)
    vacio = {"control": None, "periodos": [], "filas": [], "metrica": metrica}
    # meses closed months plus the extra base month and MTD: bound the
    # read to that window instead of the whole book.
    tope = _ultima_fecha()
    if tope is None:
        return vacio
    ancla = min(pd.to_datetime(fecha).date(), tope) if fecha else tope
    inicio = _primer_dia_mes(ancla, int(meses) + 2)
    df = leer([metrica], desde=inicio)
    if df.empty:
        return vacio

    fechas = sorted(df["fecha"].tolist())
    control = _fecha_control(fechas, fecha)
    if control is None:
        return vacio

    por_mes: dict[tuple, dt.date] = {}
    for f in fechas:
        if f > control:
            break
        por_mes[(f.year, f.month)] = f    # last close of each month wins
    cerrados = sorted(k for k in por_mes if k < (control.year, control.month))
    if len(cerrados) < 2:
        return vacio

    usar = cerrados[-(int(meses) + 1):]   # one extra as the first base
    periodos = []
    for i in range(1, len(usar)):
        anio, mes = usar[i]
        periodos.append({
            "clave": f"{anio:04d}-{mes:02d}",
            "etiqueta": f"{MESES_CORTOS[mes - 1]} {str(anio)[2:]}",
            "ini": por_mes[usar[i - 1]], "fin": por_mes[usar[i]],
        })
    periodos.append({"clave": "MTD", "etiqueta": "MTD",
                     "ini": por_mes[cerrados[-1]], "fin": control})

    porfecha = df.set_index("fecha")

    def valor(col, f):
        return _valor(porfecha, col, f)

    nombres = reg.nombres()
    filas = []
    for f in reg.fondos():
        compiten = [a for a in nombres if reg.opera(a, f)]
        rend = {a: {} for a in compiten}
        for per in periodos:
            for a in compiten:
                col = columna(a, f, metrica)
                rend[a][per["clave"]] = _variacion(valor(col, per["fin"]),
                                                   valor(col, per["ini"]))
        puestos = {a: {} for a in compiten}
        for per in periodos:
            k = per["clave"]
            vivos = [(a, rend[a][k]) for a in compiten if rend[a][k] is not None]
            for orden, (a, _) in enumerate(sorted(vivos, key=lambda x: -x[1]),
                                           start=1):
                puestos[a][k] = orden
        for a in compiten:
            filas.append({"fondo": f, "afp": a,
                          "puestos": puestos[a], "rendimientos": rend[a]})

    return {"control": str(control), "metrica": metrica,
            "total_afp": max((len([a for a in nombres if reg.opera(a, f)])
                              for f in reg.fondos()), default=len(nombres)),
            "periodos": [{"clave": p["clave"], "etiqueta": p["etiqueta"],
                          "desde": str(p["ini"]), "hasta": str(p["fin"])}
                         for p in periodos],
            "filas": filas}


# ---- Libro (tabla + export) -----------------------------------------------

def columnas_pedidas(metrica: str, fondo_arg: str) -> list[dict]:
    """
    Screen selection -> concrete columns, so the CSV downloads exactly
    what is on screen. Only combinations that exist: an AFP that does
    not operate a fund must not contribute a dash column.
    """
    metricas = _metricas_pedidas(metrica)

    if fondo_arg == "todos":
        fondos_sel = reg.fondos()
    else:
        try:
            f = int(fondo_arg)
            fondos_sel = [f] if f in reg.fondos() else reg.fondos()
        except (TypeError, ValueError):
            fondos_sel = reg.fondos()

    return [{"col": columna(a, f, m), "afp": a, "fondo": f, "metrica": m}
            for f in fondos_sel for a in reg.nombres() if reg.opera(a, f)
            for m in metricas]


def _corte_reciente(metricas: list[str], n: int, hasta=None):
    """
    First date of the last `n` distinct dates carrying any of the given
    metrics - resolved in SQL so a 60-row view does not fetch and pivot
    the book since 1993 to keep 60 dates.
    """
    fields = [reg.METRICA_FIELD[m] for m in metricas]
    sql = """
        SELECT DISTINCT fp.date
        FROM fact_prices fp
        JOIN series_registry sr ON sr.series_id = fp.series_id
        JOIN dim_entity e ON e.entity_id = sr.entity_id
        WHERE e.procode LIKE 'SPP\\_%%'
          AND sr.source = %s AND sr.field = ANY(%s)
    """
    params: list = [reg.SOURCE_SBS, fields]
    if hasta:
        sql += " AND fp.date <= %s::date"
        params.append(str(hasta))
    sql += " ORDER BY fp.date DESC LIMIT %s"
    params.append(int(n))
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return rows[-1]["date"] if rows else None


def tabla(limite, metrica: str, fondo_arg: str) -> dict:
    columnas_sel = columnas_pedidas(metrica, fondo_arg)
    metricas = _metricas_pedidas(metrica)
    desde = _corte_reciente(metricas, limite) if limite else None
    df = leer(metricas, desde=desde)
    if df.empty:
        return {"columnas": columnas_sel, "metrica": metrica,
                "fondo": fondo_arg, "filas": []}

    recorte = (df if limite is None else df.tail(int(limite))).iloc[::-1]
    nombres_col = [c["col"] for c in columnas_sel]
    filas = []
    for _, fila in recorte.iterrows():
        filas.append({"fecha": str(fila["fecha"]),
                      "valores": [None if (c not in df.columns or pd.isna(fila[c]))
                                  else float(fila[c]) for c in nombres_col]})
    return {"columnas": columnas_sel, "metrica": metrica,
            "fondo": fondo_arg, "filas": filas}


def tabla_indice(tipo: str, limite) -> dict:
    """
    Un indice compuesto sobre la MISMA rejilla de fechas del libro.

    La pregunta que responde esta tabla es "que dias falta el indice",
    asi que las filas son los dias con valor cuota - no los dias que el
    indice tiene. Si se listaran solo estos ultimos, un dia sin indice
    simplemente no apareceria, que es justo lo que hay que ver.
    """
    from src.pipelines.prices.sbs.valor_cuota.benchmark import (columna_indice,
                                                                leer_indice)
    tipo = reg.tipo_indice(tipo)
    fondos_b = reg.fondos_indice(tipo)
    columnas_sel = [{"col": columna_indice(tipo, f), "fondo": f, "afp": f"Fondo {f}",
                     "metrica": tipo} for f in fondos_b]

    desde = _corte_reciente(["valor_cuota"], limite) if limite else None
    libro = leer(["valor_cuota"], desde=desde)
    if libro.empty:
        return {"columnas": columnas_sel, "filas": [], "faltantes": 0,
                "fechas": 0}

    rejilla = (libro if limite is None else libro.tail(int(limite)))["fecha"]
    bench = leer_indice(tipo, desde=rejilla.min(), hasta=rejilla.max())
    por_fecha = {}
    if not bench.empty:
        for _, fila in bench.iterrows():
            por_fecha[fila["fecha"]] = fila

    nombres_col = [c["col"] for c in columnas_sel]
    filas, faltantes = [], 0
    for fecha in rejilla.iloc[::-1]:
        fila = por_fecha.get(fecha)
        valores = []
        for col in nombres_col:
            v = None
            if fila is not None and col in bench.columns and not pd.isna(fila[col]):
                v = float(fila[col])
            valores.append(v)
        if any(v is None for v in valores):
            faltantes += 1
        filas.append({"fecha": str(fecha), "valores": valores})
    return {"columnas": columnas_sel, "filas": filas,
            "faltantes": faltantes, "fechas": len(filas)}


def exportar_csv(metrica: str, fondo_arg: str, limite,
                 desde=None, hasta=None) -> bytes:
    """The same cut as tabla(), as CSV bytes built in memory."""
    columnas_sel = [c["col"] for c in columnas_pedidas(metrica, fondo_arg)]
    metricas = _metricas_pedidas(metrica)
    if limite:
        corte = _corte_reciente(metricas, limite, hasta=hasta)
        if corte is not None:
            desde = max(pd.to_datetime(desde).date(), corte) if desde else corte
    df = leer(metricas, desde, hasta)
    presentes = [c for c in columnas_sel if c in df.columns]
    df = df[["fecha"] + presentes] if not df.empty else df
    if limite and not df.empty:
        df = df.tail(int(limite))
    if not df.empty:
        df = df.sort_values("fecha", ascending=False)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")
