# web/api/routes/formatos.py
# ---------------------------------------------------------------------------
# /api/formatos/{clave} : que columnas espera cada sitio que recibe un
# archivo, derivado de los propios lectores.
#
# Vive en su propia ruta y no colgando de cada modulo porque la pantalla que
# lo consume es una sola: el icono de ayuda junto a cada campo de subida.
# ---------------------------------------------------------------------------
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from web.api.services import formatos as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/formatos", tags=["formatos"])


@router.get("")
def listar() -> dict:
    return {"formatos": sorted(svc.FORMATOS)}


@router.get("/{clave}")
def obtener(clave: str) -> JSONResponse:
    try:
        return JSONResponse(svc.formato(clave))
    except ValueError as exc:
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=404)
    except Exception as exc:
        logger.exception("descripcion de formato")
        return JSONResponse({"ok": False, "motivo": str(exc)}, status_code=500)
