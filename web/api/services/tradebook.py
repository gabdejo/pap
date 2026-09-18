# web/api/services/tradebook.py
# ---------------------------------------------------------------------------
# Lo que el Panel del Tradebook necesita: el volumen operado agregado por
# periodo, y su composicion por fondo, moneda, contraparte, trader y lado.
#
# El libro trae dos origenes con distinto grano - FMS, general y sin dueño, y
# el registro de los traders, detallado y con nombre - asi que casi todo lo
# de aqui se puede mirar por origen. Mezclarlos sin decirlo contaria dos veces
# la misma operacion cuando las dos vias cubran el mismo dia.
#
# La agregacion vive aqui y no en el modulo de dominio porque es una lectura
# de pantalla: que se agrupe por mes o por semana, o cuantas contrapartes
# entren en el "resto", son decisiones de como se mira el libro, no de que
# es una operacion.
# ---------------------------------------------------------------------------
from __future__ import annotations

import datetime as dt
import logging

from src.db.connection import get_connection

logger = logging.getLogger(__name__)

# date_trunc del periodo, con su etiqueta. Se listan aqui y no se aceptan
# desde la peticion tal cual: el valor entra en el SQL por interpolacion y
# una lista cerrada es lo que lo hace seguro.
PERIODOS = {
    "dia": ("day", "%d/%m/%Y"),
    "semana": ("week", "%d/%m/%Y"),
    "mes": ("month", "%m/%Y"),
    "trimestre": ("quarter", "%m/%Y"),
    "anio": ("year", "%Y"),
}
POR_DEFECTO = "mes"

# Cuantas contrapartes se nombran antes de agrupar el resto. Un grafico con
# treinta rebanadas no dice mas que uno con ocho.
TOPE_CONTRAPARTES = 8


# Los origenes que llevan dueño. 'traders' es como el operador piensa el
# libro; en la base son dos valores, y traducirlo en un sitio evita que cada
# consulta rearme la lista.
ORIGENES_TRADER = ("excel", "manual")


def _filtros(desde, hasta, fondo, moneda, lado, trader=None,
             origen=None) -> tuple[str, list]:
    donde, args = ["1 = 1"], []
    if desde:
        donde.append("fecha >= %s"); args.append(desde)
    if hasta:
        donde.append("fecha <= %s"); args.append(hasta)
    if fondo is not None:
        donde.append("fondo = %s"); args.append(int(fondo))
    if moneda:
        donde.append("moneda = %s"); args.append(str(moneda).upper())
    if lado:
        donde.append("lado = %s"); args.append(str(lado).lower())
    if trader:
        donde.append("trader = %s"); args.append(trader)
    if origen == "traders":
        donde.append("origen = ANY(%s)"); args.append(list(ORIGENES_TRADER))
    elif origen:
        donde.append("origen = %s"); args.append(origen)
    return " AND ".join(donde), args


def resumen(desde=None, hasta=None, fondo=None, moneda=None, lado=None,
            trader=None, origen=None, periodo: str = POR_DEFECTO) -> dict:
    """
    Traded volume over time plus its composition, all under the same
    filters, so every panel on the screen is talking about the same set
    of operations.

    Amounts are NOT converted between currencies. There is no rate in
    this store, and adding soles to dollars because both are numbers is
    the kind of total that looks right and is not. The currency filter
    is what makes the totals comparable, and the panel says which one is
    on screen.
    """
    periodo = periodo if periodo in PERIODOS else POR_DEFECTO
    trunc, _fmt = PERIODOS[periodo]
    donde, args = _filtros(desde, hasta, fondo, moneda, lado, trader, origen)
    vacio = {"periodo": periodo, "series": [], "por_fondo": [],
             "por_moneda": [], "por_contraparte": [], "por_instrumento": [],
             "por_trader": [], "por_origen": [],
             "total": {"operaciones": 0, "compras": 0, "ventas": 0,
                       "monedas": []}}

    with get_connection() as conn:
        if not conn.execute(f"SELECT 1 FROM tradebook WHERE {donde} LIMIT 1",
                            tuple(args)).fetchone():
            return vacio

        # date_trunc devuelve timestamp; se recorta a fecha para que el eje
        # del grafico no cargue una hora que no significa nada.
        series = conn.execute(f"""
            SELECT date_trunc('{trunc}', fecha)::date AS periodo, lado, moneda,
                   COUNT(*) AS operaciones, SUM(monto) AS monto
            FROM tradebook WHERE {donde}
            GROUP BY 1, 2, 3 ORDER BY 1""", tuple(args)).fetchall()

        def agrupado(columna, limite=None):
            sql = f"""
                SELECT COALESCE({columna}::text, 'sin dato') AS clave, moneda,
                       COUNT(*) AS operaciones, SUM(monto) AS monto
                FROM tradebook WHERE {donde}
                GROUP BY 1, 2 ORDER BY SUM(monto) DESC"""
            filas = conn.execute(sql, tuple(args)).fetchall()
            salida = [{"clave": f["clave"], "moneda": f["moneda"],
                       "operaciones": int(f["operaciones"]),
                       "monto": float(f["monto"])} for f in filas]
            return _con_resto(salida, limite) if limite else salida

        por_fondo = agrupado("fondo")
        por_moneda = agrupado("moneda")
        por_contraparte = agrupado("contraparte", TOPE_CONTRAPARTES)
        por_instrumento = agrupado("instrumento", TOPE_CONTRAPARTES)
        # Sin tope: los traders son pocos y conocidos, y esconder uno en un
        # 'Otras' seria esconder justo a quien se quiere mirar. Las filas
        # 'sin dato' son las de FMS, que no llevan nombre.
        por_trader = agrupado("trader")
        por_origen = agrupado("origen")

        total = conn.execute(f"""
            SELECT COUNT(*) AS operaciones,
                   COUNT(*) FILTER (WHERE lado = 'compra') AS compras,
                   COUNT(*) FILTER (WHERE lado = 'venta') AS ventas,
                   COUNT(*) FILTER (WHERE origen = 'fms') AS de_fms,
                   COUNT(*) FILTER (WHERE origen <> 'fms') AS de_traders,
                   COUNT(DISTINCT contraparte) AS contrapartes,
                   COUNT(DISTINCT instrumento) AS instrumentos,
                   COUNT(DISTINCT trader) AS traders,
                   MIN(fecha) AS desde, MAX(fecha) AS hasta
            FROM tradebook WHERE {donde}""", tuple(args)).fetchone()
        montos = conn.execute(f"""
            SELECT moneda, lado, SUM(monto) AS monto
            FROM tradebook WHERE {donde}
            GROUP BY 1, 2 ORDER BY 1, 2""", tuple(args)).fetchall()

    t = dict(total)
    t["desde"] = str(t["desde"]) if t["desde"] else None
    t["hasta"] = str(t["hasta"]) if t["hasta"] else None
    t["monedas"] = [{"moneda": m["moneda"], "lado": m["lado"],
                     "monto": float(m["monto"])} for m in montos]

    return {
        "periodo": periodo,
        "series": [{"periodo": str(s["periodo"]), "lado": s["lado"],
                    "moneda": s["moneda"], "operaciones": int(s["operaciones"]),
                    "monto": float(s["monto"])} for s in series],
        "por_fondo": por_fondo,
        "por_moneda": por_moneda,
        "por_contraparte": por_contraparte,
        "por_instrumento": por_instrumento,
        "por_trader": por_trader,
        "por_origen": por_origen,
        "total": t,
    }


def _con_resto(filas: list[dict], limite: int) -> list[dict]:
    """
    Los `limite` primeros por monto, y el resto sumado en una fila.

    El corte es POR MONEDA: juntar el resto de soles con el resto de
    dolares en una sola fila 'Otras' produciria el unico total que este
    modulo se niega a calcular.
    """
    salida, sobrantes = [], {}
    vistos: dict[str, int] = {}
    for f in filas:
        n = vistos.get(f["moneda"], 0)
        if n < limite:
            salida.append(f)
            vistos[f["moneda"]] = n + 1
        else:
            r = sobrantes.setdefault(
                f["moneda"], {"clave": "Otras", "moneda": f["moneda"],
                              "operaciones": 0, "monto": 0.0, "agrupadas": 0})
            r["operaciones"] += f["operaciones"]
            r["monto"] += f["monto"]
            r["agrupadas"] += 1
    return salida + list(sobrantes.values())


def rango_por_defecto() -> dict:
    """
    El ultimo año del libro, o el ultimo año natural si esta vacio.

    Se calcula sobre lo que HAY y no sobre la fecha de hoy: un libro que
    se dejo de cargar en marzo abriria vacio con un rango de hoy, y el
    operador vería "sin operaciones" en vez de sus datos.
    """
    with get_connection() as conn:
        f = conn.execute("SELECT MIN(fecha) AS desde, MAX(fecha) AS hasta "
                         "FROM tradebook").fetchone()
    hasta = f["hasta"] or dt.date.today()
    desde = max(f["desde"] or hasta, hasta - dt.timedelta(days=365))
    return {"desde": str(desde), "hasta": str(hasta),
            "hay_datos": f["hasta"] is not None}
