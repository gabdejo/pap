
# web/api/routes/spp_bloomberg.py
# ---------------------------------------------------------------------------
# /api/spp/bloomberg : the manual Bloomberg series registry ported from the
# monitor - registry CRUD, bulk registration by file, download trigger, data
# for charts, template and export.
#
# Every route answers even on machines without a terminal (deliberate: the
# dashboard runs on several machines and only some have Bloomberg); the ones
# that download return 503 with the reason, which is what the UI shows.
# The download runs in the shared background task; the UI follows it by
# polling /api/spp/tarea.
# ---------------------------------------------------------------------------
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from fastapi.responses import JSONResponse

from src.pipelines.prices.bloomberg import manual_series as bbg
from web.api.routes._spp_comun import (XLSX, es_si, fecha_iso, leer_archivo,
                                       parse_ids)
from web.api.services.spp_tarea import estado_tarea, lanzar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/spp", tags=["spp-bloomberg"])


@router.get("/tarea")
def get_tarea() -> dict:
    return estado_tarea()


@router.get("/bloomberg")
def get_bloomberg() -> dict:
    """Module state: availability plus the registry with its coverage."""
    return bbg.estado()


@router.post("/bloomberg/serie")
def post_serie(datos: dict) -> JSONResponse:
    """
    Registers or updates a series. Idempotent by (ticker, campo,
    intervalo): resending updates instead of duplicating, and what is
    not sent is not erased.
    """
    try:
        serie = bbg.registrar_serie(
            ticker=datos.get("ticker"),
            campo=datos.get("campo") or bbg.CAMPO_POR_DEFECTO,
            intervalo=datos.get("intervalo") or "diario",
            descripcion=datos.get("descripcion") or None,
            moneda=datos.get("moneda") or None,
            fecha_inicio=datos.get("fecha_inicio") or None,
            activa=datos.get("activa"),
        )
        return JSONResponse({"ok": True, "serie": serie})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("alta de serie Bloomberg")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.delete("/bloomberg/serie/{serie_id}")
def delete_serie(serie_id: int, datos: str = Query("")) -> JSONResponse:
    """Refuses when the series has data unless ?datos=1 confirms it."""
    con_datos = es_si(datos)
    try:
        return JSONResponse({"ok": True,
                             "resultado": bbg.borrar_serie(serie_id, con_datos)})
    except ValueError as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=409)
    except Exception as exc:
        logger.exception("baja de serie Bloomberg")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.post("/bloomberg/archivo")
async def post_archivo(archivo: UploadFile = File(...),
                       revisar: str = Form(""),
                       hoja: str = Form("")) -> JSONResponse:
    """
    Bulk registration from an Excel/CSV. With revisar=1 only reports what
    was read - nothing registers without having been shown first.
    """
    crudo, error = await leer_archivo(archivo)
    if error:
        return error
    solo_revisar = es_si(revisar)
    try:
        if solo_revisar:
            informe = bbg.leer_archivo_series(crudo, hoja=hoja.strip() or None)
        else:
            informe = bbg.registrar_series_archivo(crudo, hoja=hoja.strip() or None,
                                                   log=lambda m: None)
            informe.pop("series", None)
        informe["archivo"] = archivo.filename
        return JSONResponse({"ok": True, "revisado": solo_revisar,
                             "informe": informe})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("alta masiva Bloomberg")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.post("/bloomberg/extraer")
def post_extraer(datos: dict | None = None) -> JSONResponse:
    """Downloads what is missing, in the background task."""
    puede, motivo = bbg.disponible()
    if not puede:
        return JSONResponse({"ok": False, "motivo": motivo}, status_code=503)

    datos = datos or {}
    ids = datos.get("series") or None
    if ids is not None:
        try:
            ids = [int(x) for x in ids]
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "motivo": "Lista de series no valida."},
                                status_code=400)

    ok, motivo = lanzar("descarga de Bloomberg", bbg.extraer,
                        serie_ids=ids, corregir=bool(datos.get("corregir")))
    return JSONResponse({"ok": ok, "motivo": motivo},
                        status_code=200 if ok else 409)


@router.get("/bloomberg/datos")
def get_datos(series: str = Query(""),
              desde: str | None = Query(None),
              hasta: str | None = Query(None)) -> JSONResponse:
    """Points of one or several series, to chart them."""
    ids, error = parse_ids(series)
    if error:
        return error

    df = bbg.leer(ids, fecha_iso(desde), fecha_iso(hasta))
    registro = {s["serie_id"]: s for s in bbg.series_registro()}
    salida = []
    for sid, grupo in (df.groupby("serie_id") if not df.empty else []):
        s = registro.get(int(sid), {})
        salida.append({
            "serie_id": int(sid),
            "ticker": s.get("ticker", str(sid)),
            "campo": s.get("campo"),
            "intervalo": s.get("intervalo"),
            "descripcion": s.get("descripcion"),
            "puntos": [[str(f), float(v)] for f, v in
                       zip(grupo["fecha"], grupo["valor"])],
        })
    return JSONResponse({"series": salida})


@router.get("/bloomberg/plantilla")
def get_plantilla() -> Response:
    return Response(bbg.plantilla_series(), media_type=XLSX,
                    headers={"Content-Disposition":
                             "attachment; filename=plantilla_series_bloomberg.xlsx"})


@router.get("/bloomberg/exportar")
def get_exportar(series: str = Query(""),
                 desde: str | None = Query(None),
                 hasta: str | None = Query(None)):
    ids, error = parse_ids(series)
    if error:
        return error
    datos = bbg.exportar_datos(ids, fecha_iso(desde), fecha_iso(hasta))
    return Response(datos, media_type=XLSX,
                    headers={"Content-Disposition":
                             "attachment; filename=bloomberg.xlsx"})
