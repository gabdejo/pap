
# web/api/routes/_spp_comun.py
# ---------------------------------------------------------------------------
# Shared plumbing of the SPP routers (spp_carga, spp_bloomberg): the upload
# limits, the truthy-flag convention of the monitor's forms, the read-and-
# validate of an uploaded file, and the comma-separated id parser. One owner
# for each so the two routers cannot drift.
# ---------------------------------------------------------------------------
from __future__ import annotations

from datetime import date

from fastapi import UploadFile
from fastapi.responses import JSONResponse

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_SUBIDA = 40 * 10**6  # the SBS historical Excel is ~5 MB


def fecha_iso(crudo) -> str | None:
    """
    ISO date from the URL, or None. Malformed browser input is treated
    as absent - never forwarded into a ::date cast or pd.to_datetime
    where it would become a 500.
    """
    try:
        return date.fromisoformat(str(crudo).strip()).isoformat() if crudo else None
    except ValueError:
        return None


def es_si(flag: str | None) -> bool:
    """The monitor's truthy convention for form flags."""
    return str(flag or "").lower() in ("1", "true", "si")


async def leer_archivo(archivo: UploadFile) -> tuple[bytes | None, JSONResponse | None]:
    """
    Reads and validates an upload. Returns (bytes, None) or
    (None, ready-to-return 400 response).
    """
    crudo = await archivo.read()
    if not crudo:
        return None, JSONResponse(
            {"ok": False, "motivo": "No llego ningun archivo."}, status_code=400)
    if len(crudo) > MAX_SUBIDA:
        return None, JSONResponse(
            {"ok": False, "motivo":
             f"El archivo pesa {round(len(crudo) / 1e6, 1)} MB; "
             f"el limite son {MAX_SUBIDA // 10**6} MB."},
            status_code=400)
    return crudo, None


def parse_ids(crudo: str | None) -> tuple[list[int] | None, JSONResponse | None]:
    """
    Comma-separated series ids from the URL -> list[int] (None when
    empty), or (None, ready-to-return 400).
    """
    crudo = (crudo or "").strip()
    if not crudo:
        return None, None
    try:
        return [int(x) for x in crudo.split(",") if x.strip()], None
    except ValueError:
        return None, JSONResponse(
            {"ok": False, "motivo": "Lista de series no valida."}, status_code=400)
