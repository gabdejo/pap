# web/api/services/formatos.py
# ---------------------------------------------------------------------------
# Que columnas espera cada sitio que recibe un archivo: lo justo para pintar
# el cuadro de la ventana - los encabezados y una fila de ejemplo.
#
# Los NOMBRES de esas columnas salen de las mismas constantes que usa el
# lector para parsear, no de una lista escrita aparte. Una escrita aparte se
# desincroniza la primera vez que alguien toca un lector, y entonces miente -
# que es peor que no estar, porque el operador la sigue y el archivo se le cae
# sin entender por que.
#
# Lo unico escrito a mano es la fila de ejemplo, que no se puede derivar de
# nada, y el historico de la SBS: ahi no hay lector de columnas que consultar
# - es el Excel publicado por la SBS, con su disposicion propia - asi que en
# vez de cuadro lleva `notas`, que es lo que la ventana pinta cuando no hay
# columnas que ensenar.
# ---------------------------------------------------------------------------
from __future__ import annotations

import datetime as dt

from src.pipelines.prices.bloomberg import manual_series as bbg
from src.pipelines.tradebook import operaciones as tb


def _columna(nombre, obligatoria, ejemplo=None) -> dict:
    """
    Lo que la ventana pinta de cada columna, y nada mas.

    Hubo aqui tambien una descripcion y la lista de alias que el lector
    acepta. Salieron con la ventana que los mostraba: el cuadro con los
    encabezados y una fila de ejemplo ya dice el formato, y quien necesite
    los nombres exactos baja la plantilla, que los trae escritos.
    """
    return {"nombre": nombre, "obligatoria": obligatoria, "ejemplo": ejemplo}


EJEMPLO_TRADEBOOK = {
    "fecha": "10/09/2026", "fondo": "2", "lado": "compra",
    "instrumento": "PERU 3.55 03/31", "cantidad": "1,000,000", "precio": "98.45",
    "monto": "984,500.00", "moneda": "USD", "contraparte": "BCP",
    "referencia": "OP00123",
    "trader": "R. SACHS", "nota": "", "operador": "M. LOPEZ",
}


def _tradebook(origen: str) -> dict:
    de_traders = origen in tb.ORIGENES_TRADER
    columnas = []
    for nombre in tb._ALIAS:
        if nombre == "trader" and not de_traders:
            continue
        obligatoria = (nombre in tb._OBLIGATORIAS
                       or (nombre == "trader" and de_traders))
        columnas.append(_columna(tb.encabezado(nombre), obligatoria,
                                 EJEMPLO_TRADEBOOK.get(nombre)))
    return {
        "titulo": ("Operaciones · registro del trader" if de_traders
                   else "Operaciones · reporte de FMS"),
        "columnas": columnas,
        "plantilla": f"/api/tradebook/plantilla?origen={origen}",
    }


def _series_manuales() -> dict:
    return {
        "titulo": "Valores de una serie manual",
        "columnas": [
            _columna("fecha", True, "10/09/2026"),
            _columna("valor", True, "1,234.5678"),
        ],
        "plantilla": "/api/spp/series-manuales/plantilla",
    }


def _bloomberg() -> dict:
    ejemplo = {"ticker": "SPX Index", "campo": "PX_LAST", "intervalo": "diario",
               "fecha_inicio": "01/01/2016", "descripcion": "S&P 500",
               "moneda": "USD"}
    return {
        "titulo": "Series de Bloomberg a registrar",
        "columnas": [
            _columna(c, c == "ticker", ejemplo.get(c))
            for c in bbg.CABECERA_SERIES
        ],
        "plantilla": "/api/spp/bloomberg/plantilla",
    }


def _valor_cuota_historico() -> dict:
    """
    El unico que no sale de un lector de columnas: es el Excel que publica
    la SBS, y lo que hay que decir del formato es que no se toque.
    """
    return {
        "titulo": "Historico de valor cuota (archivo de la SBS)",
        "columnas": [],
        "notas": [
            "Descargalo con el boton «Abrir la pagina de la SBS» que esta "
            "aqui al lado, y elige «Valores cuota desde Agosto 1993».",
            "Trae las cuatro AFP y los cuatro tipos de fondo en la misma "
            "hoja; el lector los separa solo.",
            "Las fechas futuras se descartan y se avisa de ellas.",
            "Subirlo NO carga nada todavia: primero muestra que cambiaria, y "
            "la carga se confirma despues.",
        ],
        "plantilla": None,
    }


FORMATOS = {
    "tradebook_traders": lambda: _tradebook("excel"),
    "tradebook_fms": lambda: _tradebook("fms"),
    "series_manuales": _series_manuales,
    "bloomberg": _bloomberg,
    "valor_cuota_historico": _valor_cuota_historico,
}


def formato(clave: str) -> dict:
    if clave not in FORMATOS:
        raise ValueError(
            f"No hay un formato llamado '{clave}'. Los que hay: "
            + ", ".join(sorted(FORMATOS)))
    return {"clave": clave, **FORMATOS[clave]()}
