# web/api/routes/tradebook.py
# ---------------------------------------------------------------------------
# /api/tradebook : las operaciones de la mesa.
#
#   Panel            GET  /resumen, /estado
#   Registro y carga GET/POST/PUT/DELETE /operaciones, carga por archivo
#                    (las dos vias, FMS y el registro del trader), plantilla
#                    y exportacion.
#
# Mismo contrato de errores que el resto del tablero: nunca se lanza una
# excepcion a la interfaz, se devuelve {ok, motivo} con el codigo que
# corresponde, para que la pantalla pueda decir que paso.
# ---------------------------------------------------------------------------
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from fastapi.responses import JSONResponse

from src.pipelines.tradebook import operaciones as tb
from web.api.routes._spp_comun import XLSX, es_si, fecha_iso, leer_archivo
from web.api.services import tradebook as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tradebook", tags=["tradebook"])


def _entero(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---- Panel ----------------------------------------------------------------

@router.get("/estado")
def get_estado() -> dict:
    """What the book holds: rows, span, funds, currencies, origins."""
    return {**tb.estado(), "rango": svc.rango_por_defecto(),
            "periodos": list(svc.PERIODOS)}


@router.get("/resumen")
def get_resumen(desde: str | None = Query(None), hasta: str | None = Query(None),
                fondo: str | None = Query(None), moneda: str | None = Query(None),
                lado: str | None = Query(None), trader: str | None = Query(None),
                origen: str | None = Query(None),
                periodo: str = Query(svc.POR_DEFECTO)) -> JSONResponse:
    """Aggregated activity under the filters the panel is showing."""
    try:
        return JSONResponse(svc.resumen(
            desde=fecha_iso(desde), hasta=fecha_iso(hasta),
            fondo=_entero(fondo), moneda=moneda or None, lado=lado or None,
            trader=trader or None, origen=origen or None, periodo=periodo))
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("resumen del tradebook")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


# ---- Registro y carga ------------------------------------------------------

@router.get("/operaciones")
def get_operaciones(desde: str | None = Query(None),
                    hasta: str | None = Query(None),
                    fondo: str | None = Query(None),
                    lado: str | None = Query(None),
                    contraparte: str | None = Query(None),
                    instrumento: str | None = Query(None),
                    trader: str | None = Query(None),
                    origen: str | None = Query(None),
                    limite: int = Query(500)) -> JSONResponse:
    try:
        return JSONResponse({"operaciones": tb.leer(
            desde=fecha_iso(desde), hasta=fecha_iso(hasta), fondo=_entero(fondo),
            lado=lado or None, contraparte=contraparte or None,
            instrumento=instrumento or None, trader=trader or None,
            origen=origen or None, limite=limite)})
    except ValueError as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("lectura del tradebook")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.post("/operaciones")
def post_operacion(datos: dict) -> JSONResponse:
    """Registers one operation typed by hand."""
    try:
        return JSONResponse({"ok": True,
                             "operacion": tb.registrar({**datos, "origen": "manual"})})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("alta de operacion")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.put("/operaciones/{operacion_id}")
def put_operacion(operacion_id: int, datos: dict) -> JSONResponse:
    try:
        return JSONResponse({"ok": True,
                             "operacion": tb.actualizar(operacion_id, datos)})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("correccion de operacion")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.delete("/operaciones/{operacion_id}")
def delete_operacion(operacion_id: int) -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "operacion": tb.borrar(operacion_id)})
    except ValueError as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=404)
    except Exception as exc:
        logger.exception("baja de operacion")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.post("/archivo")
async def post_archivo(archivo: UploadFile = File(...),
                       revisar: str = Form(""),
                       hoja: str = Form(""),
                       origen: str = Form("excel"),
                       trader: str = Form("")) -> JSONResponse:
    """
    Bulk load from Excel/CSV, for either book. With revisar=1 it only
    reports what it read - nothing is stored without having been shown
    first.

    `origen` picks the book ('excel' for the traders' own registration,
    'fms' for the system's); `trader` is the default for rows that do
    not name one, which is the usual shape of a file that is all one
    person's.
    """
    crudo, error = await leer_archivo(archivo)
    if error:
        return error
    solo_revisar = es_si(revisar)
    try:
        if solo_revisar:
            informe = tb.leer_archivo(crudo, hoja=hoja.strip() or None,
                                      origen=origen, trader=trader.strip() or None)
            # La lista completa solo sirve para contarla; mandarla entera
            # son megabytes por una pantalla que muestra quince filas.
            informe["muestra"] = informe.get("operaciones", [])[:15]
            informe.pop("operaciones", None)
        else:
            informe = tb.importar(crudo, hoja=hoja.strip() or None,
                                  origen=origen, trader=trader.strip() or None)
        informe["archivo"] = archivo.filename
        return JSONResponse({"ok": True, "revisado": solo_revisar,
                             "informe": informe})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("carga del tradebook")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.get("/plantilla")
def get_plantilla(origen: str = Query("excel")) -> Response:
    """The template for one book or the other - they differ by `trader`."""
    nombre = ("plantilla_tradebook_fms.xlsx" if origen == "fms"
              else "plantilla_tradebook_traders.xlsx")
    return Response(tb.plantilla(origen=origen), media_type=XLSX,
                    headers={"Content-Disposition":
                             f"attachment; filename={nombre}"})


@router.get("/exportar")
def get_exportar(desde: str | None = Query(None), hasta: str | None = Query(None),
                 fondo: str | None = Query(None), lado: str | None = Query(None),
                 contraparte: str | None = Query(None),
                 instrumento: str | None = Query(None),
                 trader: str | None = Query(None),
                 origen: str | None = Query(None)) -> Response:
    datos = tb.exportar(desde=fecha_iso(desde), hasta=fecha_iso(hasta),
                        fondo=_entero(fondo), lado=lado or None,
                        contraparte=contraparte or None,
                        instrumento=instrumento or None,
                        trader=trader or None, origen=origen or None)
    return Response(datos, media_type=XLSX,
                    headers={"Content-Disposition":
                             "attachment; filename=tradebook.xlsx"})
