
# web/api/routes/spp_carga.py
# ---------------------------------------------------------------------------
# /api/spp : write routes of the SPP tablero (phase 2). Everything that
# reaches the database through here either validates before writing or runs
# in the background task that leaves a trail. The historical load goes in two
# deliberate steps - review, then load - because writing thousands of dates
# without having seen what they bring is the kind of operation one only
# regrets afterwards.
# ---------------------------------------------------------------------------
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, File, Form, Response, UploadFile
from fastapi.responses import JSONResponse

from src.configs.machine_config import machine_id, scraper_enabled
from src.pipelines.prices.manual import series as man
from src.pipelines.prices.sbs.valor_cuota import afps as reg
from src.pipelines.prices.sbs.valor_cuota import benchmark as bench
from src.pipelines.prices.sbs.valor_cuota import benchmark_composicion as bcomp
from src.pipelines.prices.sbs.valor_cuota.registro import registrar_valores
from src.pipelines.prices.sbs.valor_cuota.run import (
    MODOS_CARGA, cargar_historico, revisar_historico, run_daily, ultima_corrida)
from web.api.routes._spp_comun import XLSX, es_si, fecha_iso, leer_archivo
from web.api.services.spp_tarea import (
    TAREA_WINDOWS, con_bitacora, guardar_subida, lanzar, leer_subida)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/spp", tags=["spp-carga"])


# ---- Extraccion -----------------------------------------------------------

@router.post("/extraer")
def post_extraer(datos: dict | None = None) -> JSONResponse:
    """Launches the SBS scrape in the background task (opens Chrome)."""
    if not scraper_enabled():
        return JSONResponse(
            {"ok": False, "motivo":
             f"El scraper no esta habilitado en esta maquina ({machine_id()}). "
             "Pon SCRAPER_ENABLED=true en el archivo .env del proyecto y "
             "reinicia la API (la configuracion se cachea al arrancar)."},
            status_code=503)
    refrescar = bool((datos or {}).get("refrescar"))
    ok, motivo = lanzar("extraccion SBS",
                        con_bitacora(run_daily, refresh=refrescar))
    return JSONResponse({"ok": ok, "motivo": motivo},
                        status_code=200 if ok else 409)


@router.get("/programado")
def get_programado() -> dict:
    """What Windows says about the scheduled task, and how the last
    unattended run went."""
    return {"tarea": _tarea_windows(), "ultima": ultima_corrida(),
            "nombre": TAREA_WINDOWS}


def _tarea_windows() -> dict:
    """
    Queried via PowerShell, not schtasks: Get-ScheduledTaskInfo property
    names are stable, while schtasks output is translated per system
    language.
    """
    if os.name != "nt":
        return {"disponible": False, "motivo": "Solo en Windows."}
    # Timestamps go out as ISO ('s' = yyyy-MM-ddTHH:mm:ss), NEVER as
    # [string]$date: that cast formats with the powershell process's
    # culture, which varies per machine (dd/MM vs MM/dd) and made the
    # dashboard swap day and month depending on where the API ran.
    guion = (
        f"$t = Get-ScheduledTask -TaskName '{TAREA_WINDOWS}' -ErrorAction SilentlyContinue; "
        "if (-not $t) { '{\"registrada\":false}' } else { "
        "$i = $t | Get-ScheduledTaskInfo; "
        "[pscustomobject]@{ registrada=$true; estado=[string]$t.State; "
        "  proxima=if ($i.NextRunTime) { $i.NextRunTime.ToString('s') } else { '' }; "
        "  ultima=if ($i.LastRunTime) { $i.LastRunTime.ToString('s') } else { '' }; "
        "  resultado=$i.LastTaskResult; omitidas=$i.NumberOfMissedRuns; "
        "  ejecutable=[string]$t.Actions[0].Execute; "
        "  argumentos=[string]$t.Actions[0].Arguments; "
        "  disparo=[string]($t.Triggers | ForEach-Object { $_.StartBoundary }) "
        "} | ConvertTo-Json -Compress }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", guion],
                           capture_output=True, text=True, timeout=25)
        datos = json.loads((r.stdout or "").strip() or '{"registrada":false}')
        datos["disponible"] = True
        if datos.get("registrada"):
            datos["apunta_aqui"] = _apunta_aqui(datos.get("ejecutable"))
        return datos
    except Exception as exc:
        return {"disponible": False, "motivo": str(exc)[:200]}


def _apunta_aqui(ejecutable: str | None) -> bool:
    """
    Whether the registered task runs THIS copy of the project.

    On the machine without git every update is a fresh zip in a new
    folder, so it is easy to end up with the task still driving the old
    copy (or a deleted one) while the tablero reports the automation as
    healthy. The task's python is the repo's own .venv, which is also
    the interpreter serving this API.
    """
    if not ejecutable:
        return True
    try:
        return Path(ejecutable).resolve() == Path(sys.executable).resolve()
    except Exception:
        return True


# ---- Registro manual ------------------------------------------------------

@router.post("/valor")
def post_valor(datos: dict) -> JSONResponse:
    """
    Manual registration of one AFP on one date, all its funds at once.
    `valores` is keyed by fund: {"0": 15.87, "2": null, ...} - null
    deletes that value, an absent fund is left as is.
    """
    try:
        resultado = registrar_valores(
            datos.get("fecha"), datos.get("afp"),
            datos.get("valores") or {},
            datos.get("metrica") or "valor_cuota")
        return JSONResponse({"ok": True, "resultado": resultado})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("registro manual")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


# ---- Carga historica (dos pasos) ------------------------------------------

@router.post("/historico/revisar")
async def post_historico_revisar(archivo: UploadFile = File(...)) -> JSONResponse:
    """
    First half: review the Excel without writing. The file is kept in
    memory under a vale so confirming loads exactly what was reviewed.
    """
    crudo, error = await leer_archivo(archivo)
    if error:
        return error
    try:
        informe = revisar_historico(crudo)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("revision del historico")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)

    vale = guardar_subida(crudo, archivo.filename)
    informe["archivo"] = archivo.filename
    informe["bytes"] = len(crudo)
    return JSONResponse({"ok": True, "vale": vale, "informe": informe})


@router.post("/historico/cargar")
def post_historico_cargar(datos: dict) -> JSONResponse:
    """Second half: write the reviewed file, in the chosen mode."""
    vale = datos.get("vale")
    modo = (datos.get("modo") or "faltantes").strip()
    if modo not in MODOS_CARGA:
        return JSONResponse({"ok": False, "motivo": f"Modo no valido: {modo}"},
                            status_code=400)
    guardado = leer_subida(vale)
    if guardado is None:
        return JSONResponse(
            {"ok": False, "motivo": "El archivo revisado ya no esta en memoria. "
                                    "Vuelve a subirlo."},
            status_code=410)

    ok, motivo = lanzar(f"carga historica ({modo})",
                        con_bitacora(cargar_historico,
                                     contenido=guardado["datos"], modo=modo))
    return JSONResponse({"ok": ok, "motivo": motivo},
                        status_code=200 if ok else 409)


# ---- Series manuales (componentes fuera de Bloomberg) ---------------------
# The store for benchmark component prices no vendor provides. Levels of
# the benchmark itself are never loaded through here (nor anywhere): they
# are calculated from the composition.

@router.get("/series-manuales")
def get_series_manuales() -> dict:
    return {"series": man.series()}


@router.post("/series-manuales")
def post_serie_manual(datos: dict) -> JSONResponse:
    """Registers or updates a series; idempotent by nombre."""
    try:
        serie = man.registrar_serie(datos.get("nombre"),
                                    datos.get("descripcion"),
                                    datos.get("moneda"))
        return JSONResponse({"ok": True, "serie": serie})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("alta de serie manual")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.delete("/series-manuales/{serie_id}")
def delete_serie_manual(serie_id: int, datos: str = "") -> JSONResponse:
    """Refuses when the series has data unless ?datos=1 confirms it,
    and always refuses while a composition references it."""
    try:
        return JSONResponse({"ok": True,
                             "resultado": man.borrar_serie(serie_id, es_si(datos))})
    except ValueError as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=409)
    except Exception as exc:
        logger.exception("baja de serie manual")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.get("/series-manuales/{serie_id}/datos")
def get_serie_manual_datos(serie_id: int) -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "puntos": man.leer(serie_id)})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)


@router.post("/series-manuales/{serie_id}/valores")
def post_serie_manual_valores(serie_id: int, datos: dict) -> JSONResponse:
    """Point entry: {fecha: valor|null} - null deletes that date."""
    try:
        resultado = man.registrar_valores(serie_id, datos.get("valores") or {})
        return JSONResponse({"ok": True, "resultado": resultado})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("registro de serie manual")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.post("/series-manuales/{serie_id}/archivo")
async def post_serie_manual_archivo(serie_id: int,
                                    archivo: UploadFile = File(...),
                                    revisar: str = Form(""),
                                    refrescar: str = Form("")) -> JSONResponse:
    """
    Load by file into one series. With revisar=1 only reports what was
    read; nothing reaches the base without having been shown first.
    """
    crudo, error = await leer_archivo(archivo)
    if error:
        return error
    solo_revisar = es_si(revisar)
    try:
        if solo_revisar:
            informe = man.leer_archivo_valores(crudo)
            informe.pop("valores", None)
        else:
            informe = man.importar_valores(serie_id, crudo,
                                           refrescar=es_si(refrescar))
        informe["archivo"] = archivo.filename
        return JSONResponse({"ok": True, "revisado": solo_revisar,
                             "informe": informe})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("carga de serie manual")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.get("/series-manuales/plantilla")
def get_serie_manual_plantilla() -> Response:
    return Response(man.plantilla(), media_type=XLSX,
                    headers={"Content-Disposition":
                             "attachment; filename=plantilla_serie_manual.xlsx"})


@router.get("/series-manuales/{serie_id}/exportar")
def get_serie_manual_exportar(serie_id: int,
                              desde: str | None = None,
                              hasta: str | None = None) -> Response:
    return Response(man.exportar_datos(serie_id, fecha_iso(desde), fecha_iso(hasta)),
                    media_type=XLSX,
                    headers={"Content-Disposition":
                             f"attachment; filename=serie_manual_{serie_id}.xlsx"})


# ---- Indices compuestos: target y benchmark -------------------------------
# Un solo juego de rutas para los dos, con el tipo en el camino. Lo que no
# sea target ni benchmark se responde 404 antes de tocar nada.

def _tipo_o_404(tipo: str):
    try:
        return reg.tipo_indice(tipo), None
    except ValueError as exc:
        return None, JSONResponse({"ok": False, "motivo": str(exc)}, status_code=404)


@router.get("/indice/{tipo}")
def get_indice(tipo: str):
    t, err = _tipo_o_404(tipo)
    if err:
        return err
    return {"estado": bench.estado_indice(t)}


@router.get("/indice/{tipo}/definiciones")
def get_definiciones(tipo: str):
    t, err = _tipo_o_404(tipo)
    if err:
        return err
    return {"indices": bcomp.leer_definiciones(t)}


@router.post("/indice/{tipo}/definicion")
def post_definicion(tipo: str, datos: dict) -> JSONResponse:
    """Crea o renombra el indice de un fondo; declararlo habilita ese fondo
    y registra su serie."""
    t, err = _tipo_o_404(tipo)
    if err:
        return err
    try:
        return JSONResponse({"ok": True, "resultado": bcomp.guardar_definicion(
            t, datos.get("fondo"), datos.get("nombre"), datos.get("descripcion"))})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("definicion de indice")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.delete("/indice/{tipo}/definicion")
def delete_definicion(tipo: str, fondo: str) -> JSONResponse:
    t, err = _tipo_o_404(tipo)
    if err:
        return err
    try:
        return JSONResponse({"ok": True,
                             "resultado": bcomp.borrar_definicion(t, fondo)})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)


@router.get("/indice/{tipo}/composicion")
def get_composicion(tipo: str, fondo: str | None = None):
    t, err = _tipo_o_404(tipo)
    if err:
        return err
    f = int(fondo) if fondo and fondo.strip().isdigit() else None
    return {"composiciones": bcomp.leer_composiciones(t, f)}


@router.get("/indice/{tipo}/series-disponibles")
def get_series_disponibles(tipo: str, q: str = ""):
    # El catalogo es el mismo para los dos tipos; el tipo esta en la ruta
    # solo para que el editor use una unica base de URL.
    t, err = _tipo_o_404(tipo)
    if err:
        return err
    return bcomp.series_disponibles(q)


@router.post("/indice/{tipo}/composicion")
def post_composicion(tipo: str, datos: dict) -> JSONResponse:
    """
    Saves one basket effective from a date. Re-posting the same
    (tipo, fondo, fecha) replaces that basket; other dates are history
    and stay untouched.
    """
    t, err = _tipo_o_404(tipo)
    if err:
        return err
    try:
        resultado = bcomp.guardar_composicion(
            t, datos.get("fondo"), datos.get("vigente_desde"),
            datos.get("componentes") or [])
        return JSONResponse({"ok": True, "resultado": resultado})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("guardar composicion de indice")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.delete("/indice/{tipo}/composicion")
def delete_composicion(tipo: str, fondo: str, vigente_desde: str) -> JSONResponse:
    t, err = _tipo_o_404(tipo)
    if err:
        return err
    try:
        return JSONResponse({"ok": True,
                             "resultado": bcomp.borrar_composicion(t, fondo, vigente_desde)})
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("borrar composicion de indice")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)


@router.post("/indice/{tipo}/recalcular")
def post_recalcular(tipo: str, datos: dict) -> JSONResponse:
    """
    Regenerates the level series of one index from its compositions, in
    the background task (the UI follows /api/spp/tarea). Replaces the
    stored series for that fund - the composition is the source of
    truth.
    """
    t, err = _tipo_o_404(tipo)
    if err:
        return err
    try:
        fondo = int(datos.get("fondo"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "motivo": "Fondo no valido."},
                            status_code=400)
    ok, motivo = lanzar(f"recalculo del {t} F{fondo}",
                        bcomp.recalcular, tipo=t, fondo=fondo)
    return JSONResponse({"ok": ok, "motivo": motivo},
                        status_code=200 if ok else 409)


@router.get("/indice/{tipo}/exportar")
def get_indice_exportar(tipo: str, desde: str | None = None,
                        hasta: str | None = None):
    t, err = _tipo_o_404(tipo)
    if err:
        return err
    return Response(bench.exportar_indice_datos(t, fecha_iso(desde), fecha_iso(hasta)),
                    media_type=XLSX,
                    headers={"Content-Disposition":
                             f"attachment; filename={t}_diario.xlsx"})
