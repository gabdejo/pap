# src/pipelines/prices/sbs/valor_cuota/registro.py
# ---------------------------------------------------------------
# Manual registration of valor cuota values: one AFP, one date, all
# its funds at once, in a single transaction - either every fund
# enters or none does.
#
# The monitor's wide-table semantics translate cleanly to long
# format: writing a value is an upsert on (series_id, date) with
# source='manual'; CLEARING a value is deleting that row (no NULL
# cells exist here), and a date with no values left is simply a date
# with no rows - it leaves the book by itself, no cleanup pass.
#
# limpiar_valores / escribir_fecha are shared with benchmark.py,
# which applies the exact same grammar to its own fund->series map:
# the validation and write semantics live once.
# ---------------------------------------------------------------

import datetime as dt
import logging
from typing import Callable

import pandas as pd

from src.db.connection import get_connection
from src.pipelines.prices.sbs.valor_cuota import afps as reg
from src.pipelines.prices.sbs.valor_cuota.afps import METRICA_FIELD
from src.pipelines.prices.sbs.valor_cuota.loader import upsert_fact

logger = logging.getLogger(__name__)


# ---- Shared grammar of the manual forms -----------------------------

def limpiar_valores(valores: dict, validar_fondo: Callable[[int], None],
                    etiqueta: str) -> dict[int, float | None]:
    """
    Normalizes a {fondo: valor} form payload: None/'' means delete, a
    number must be positive, and validar_fondo raises for funds the
    form must not accept. `etiqueta` names the value in the error
    ('El valor' / 'El benchmark').
    """
    limpios: dict[int, float | None] = {}
    for f, v in (valores or {}).items():
        f = int(f)
        validar_fondo(f)
        if v is None or v == "":
            limpios[f] = None
        else:
            v = float(v)
            if v <= 0:
                raise ValueError(f"{etiqueta} del Fondo {f} debe ser mayor que cero.")
            limpios[f] = v
    if not limpios:
        raise ValueError("No se recibio ningun valor.")
    return limpios


def fecha_existe(conn, series_ids: list[int], fecha) -> bool:
    """Whether any of the given series has a row on that date."""
    if not series_ids:
        return False
    row = conn.execute(
        "SELECT 1 FROM fact_prices WHERE date = %s AND series_id = ANY(%s) LIMIT 1",
        (fecha, series_ids)).fetchone()
    return row is not None


def escribir_fecha(conn, fecha, limpios: dict[int, float | None],
                   serie_de: Callable[[int], int],
                   ids_fecha: list[int]) -> dict:
    """
    Applies a cleaned form to one date: value -> upsert (source
    'manual'), None -> DELETE. `ids_fecha` is the series universe that
    decides whether the date existed before / has anything left after.
    Returns {guardados, borrados, fila_nueva, fila_borrada}.
    """
    existia = fecha_existe(conn, ids_fecha, fecha)
    guardados, borrados = {}, []
    for f, v in limpios.items():
        sid = serie_de(f)
        if v is None:
            cur = conn.execute(
                "DELETE FROM fact_prices WHERE series_id = %s AND date = %s",
                (sid, fecha))
            if cur.rowcount > 0:
                borrados.append(f)
        else:
            upsert_fact(conn, sid, fecha, v, "manual", refresh=True)
            guardados[f] = v
    fila_borrada = existia and not fecha_existe(conn, ids_fecha, fecha)
    return {"guardados": guardados, "borrados": borrados,
            "fila_nueva": not existia, "fila_borrada": bool(fila_borrada)}


def validar_fecha(fecha) -> dt.date:
    fecha = pd.to_datetime(fecha).date()
    if fecha > dt.date.today():
        raise ValueError("La fecha no puede ser futura.")
    return fecha


# ---- Valor cuota form -----------------------------------------------

def registrar_valores(fecha, afp: str, valores: dict,
                      metrica: str = "valor_cuota") -> dict:
    """
    Registers or corrects ALL of one AFP's funds on one date.

    `valores` is keyed by fund type. None deletes that value; a fund
    absent from the dict is left as is. Everything happens in one
    transaction.
    """
    clave = reg.clave_de(afp)
    if not clave:
        raise ValueError(f"AFP no registrada: {afp}. Agregala en config/afps.yaml.")
    nombre = reg.nombre_de(clave)
    if metrica not in METRICA_FIELD:
        raise ValueError(f"Metrica no valida: {metrica}")
    fecha = validar_fecha(fecha)

    def validar_fondo(f: int) -> None:
        if f not in reg.fondos():
            raise ValueError(f"Tipo de fondo no valido: {f}")
        if not reg.opera(clave, f):
            raise ValueError(
                f"{nombre} no opera el Fondo {f} segun config/afps.yaml.")

    limpios = limpiar_valores(valores, validar_fondo, "El valor")
    field = METRICA_FIELD[metrica]

    with get_connection() as conn:
        series_map = reg.series_map(conn)

        def serie_de(fondo: int) -> int:
            s = series_map.get((reg.procode(clave, fondo), field))
            if s is None:
                raise ValueError(
                    f"No hay serie registrada para {nombre} F{fondo} / {metrica}. "
                    "Corre scripts/run_sbs_valor_cuota.py --solo-registro.")
            return s["series_id"]

        spp_ids = [s["series_id"] for s in series_map.values()
                   if s["source"] == reg.SOURCE_SBS]
        resultado = escribir_fecha(conn, fecha, limpios, serie_de, spp_ids)

    logger.info(f"registro manual: {nombre} {metrica} {fecha} - "
                f"{len(resultado['guardados'])} guardados, "
                f"{len(resultado['borrados'])} borrados.")
    return {"fecha": str(fecha), "afp": nombre, "metrica": metrica, **resultado}
