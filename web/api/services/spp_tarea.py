
# web/api/services/spp_tarea.py
# ---------------------------------------------------------------------------
# Shared plumbing of the SPP tab's write operations:
#
#   - the single background task slot (SBS extraction, historical load,
#     Bloomberg download). One thread + one lock: a second launch is refused
#     with the name of the operation already running. The frontend polls the
#     bitacora until activa goes false - same contract as the monitor.
#   - con_bitacora(): adapter that pipes the pipeline's `logging` records
#     into the task bitacora, so pipeline code needs no log= parameter.
#   - the upload store: a reviewed file is kept IN MEMORY under a vale so
#     confirming the load writes exactly the bytes that were reviewed,
#     without a second upload. Capped at the last 3; an expired vale means
#     re-uploading (HTTP 410 at the route).
# ---------------------------------------------------------------------------
from __future__ import annotations

import logging
import threading
import traceback
from collections import deque

# The Windows scheduled-task name. Load-bearing on BOTH sides of the
# cutover: scripts/"Programar extraccion SPP.ps1" ($Tarea, line ~34)
# registers under this SAME name so it replaces the old monitor's task,
# and /api/spp/programado queries it - if the two ever diverge, the
# dashboard reports the automation dead while the scraper still runs.
# Rename here and in the .ps1 together, or never.
TAREA_WINDOWS = "Profuturo - Valor cuota SPP"

_tarea = {"activa": False, "accion": None, "bitacora": deque(maxlen=200),
          "resultado": None, "error": None}
_candado = threading.Lock()


def lanzar(accion: str, funcion, **kwargs) -> tuple[bool, str | None]:
    """Starts `funcion(log=..., **kwargs)` in a thread if none is running."""
    with _candado:
        if _tarea["activa"]:
            return False, f"Ya hay una operacion en curso: {_tarea['accion']}"
        _tarea.update(activa=True, accion=accion, resultado=None, error=None)
        _tarea["bitacora"].clear()

    def registrar(mensaje):
        _tarea["bitacora"].append(str(mensaje))

    def correr():
        try:
            registrar(f"Iniciando {accion}...")
            _tarea["resultado"] = funcion(log=registrar, **kwargs)
            registrar("Listo.")
        except Exception as exc:
            _tarea["error"] = str(exc)
            registrar(f"ERROR: {exc}")
            traceback.print_exc()
        finally:
            _tarea["activa"] = False

    threading.Thread(target=correr, daemon=True).start()
    return True, None


def estado_tarea() -> dict:
    return {"activa": _tarea["activa"], "accion": _tarea["accion"],
            "bitacora": list(_tarea["bitacora"]),
            "resultado": _tarea["resultado"], "error": _tarea["error"]}


def con_bitacora(funcion, **kwargs):
    """
    Wraps a pipeline callable so its `logging` output lands in the task
    bitacora. The pipeline logs through logging (per project convention);
    the bitacora expects a log() callback - this bridges the two without
    touching pipeline signatures.
    """
    def correr(log):
        class _Puente(logging.Handler):
            def emit(self, record):
                log(record.getMessage())

        puente = _Puente()
        raiz = logging.getLogger("src")
        raiz.addHandler(puente)
        try:
            return funcion(**kwargs)
        finally:
            raiz.removeHandler(puente)
    return correr


# ---- Upload store (vales) -------------------------------------------------

_SUBIDAS: dict[str, dict] = {}
_MAX_SUBIDAS = 3
_contador_vales = 0


def guardar_subida(datos: bytes, nombre: str) -> str:
    global _contador_vales
    with _candado:
        _contador_vales += 1
        vale = f"v{_contador_vales}"
        _SUBIDAS[vale] = {"datos": datos, "nombre": nombre}
        for viejo in list(_SUBIDAS)[:-_MAX_SUBIDAS]:
            _SUBIDAS.pop(viejo, None)
    return vale


def leer_subida(vale: str) -> dict | None:
    return _SUBIDAS.get(vale)
