# web/api/routes/benchmarks.py
# ---------------------------------------------------------------------------
# /api/benchmarks : the composite indices (benchmarks, targets) - which
# exist, their versioned definitions, the changelog, the series catalog a
# basket draws from (every price source, found by procode, name, ticker or
# ISIN, with coverage), and the writes: append a version, materialize,
# register a Bloomberg ticker.
#
# Definitions are append-only: there is no PUT or DELETE here. A change is
# POSTed as a new version with its reason; the materialization runs in the
# shared background task (the frontend follows /api/tasks/current). The
# ticker registration writes the registry only: the series is born
# 'backfill-pending' and the Bloomberg machine fetches its history.
# ---------------------------------------------------------------------------
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from src.db.onboard import SeriesConflict
from web.api.routes._spp_common import XLSX, iso_date
from web.api.services import benchmarks as svc
from web.api.services import task_slot as task

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])


class BenchmarkIn(BaseModel):
    procode: str
    display_name: str
    base_currency: str = "PEN"
    # The house fund type this index is the benchmark of (0..3); it then
    # shows inside that fund's block of the SPP return tables.
    fund: int | None = None


class ConstituentIn(BaseModel):
    label: str
    series_id: int
    weight: float
    fx_series_id: int | None = None


class VersionIn(BaseModel):
    effective_from: str
    method: str = "buy_and_hold"
    rebalance_frequency: str | None = None
    change_type: str
    reason: str
    support_doc: str | None = None
    recorded_by: str | None = None
    supersedes_version_id: int | None = None
    constituents: list[ConstituentIn]


class SeriesIn(BaseModel):
    procode: str
    name: str
    parsekyable: str
    default_start_date: str
    entity_type: str = "security"
    security_type: str | None = None
    isin: str | None = None


def _not_found(procode: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"No benchmark named {procode}.")


# ---- Reads ---------------------------------------------------------------------

@router.get("")
def list_benchmarks() -> dict:
    return {"benchmarks": svc.list_benchmarks()}


@router.get("/series")
def get_series_catalog(q: str = Query(""),
                       limit: int = Query(50, ge=1, le=200)) -> dict:
    """Pickable constituent series (the pipeline spine, no model series),
    with their coverage."""
    return {"series": svc.series_catalog(q, limit)}


@router.get("/{procode}")
def get_benchmark(procode: str) -> dict:
    bench = svc.get(procode)
    if bench is None:
        raise _not_found(procode)
    return bench


@router.get("/{procode}/versions")
def get_versions(procode: str) -> dict:
    try:
        return {"versions": svc.versions(procode)}
    except LookupError:
        raise _not_found(procode) from None


@router.get("/{procode}/changelog")
def get_changelog(procode: str) -> dict:
    try:
        return {"changelog": svc.changelog(procode)}
    except LookupError:
        raise _not_found(procode) from None


@router.get("/{procode}/levels")
def get_levels(procode: str,
               date_from: str | None = Query(None, alias="from"),
               date_to: str | None = Query(None, alias="to")) -> dict:
    """The level series as [date, level] points, for the chart."""
    result = svc.levels_points(procode, iso_date(date_from), iso_date(date_to))
    if result is None:
        raise _not_found(procode)
    return result


@router.get("/{procode}/table")
def get_table(procode: str,
              limit: int = Query(60, ge=0, le=20000, description="0 = the whole book")) -> dict:
    """The index day by day, over the book's date grid (a missing day shows)."""
    result = svc.benchmark_table(procode, limit or None)
    if result is None:
        raise _not_found(procode)
    return result


@router.get("/{procode}/export")
def get_export(procode: str,
               date_from: str | None = Query(None, alias="from"),
               date_to: str | None = Query(None, alias="to")) -> Response:
    if svc.get(procode) is None:
        raise _not_found(procode)
    return Response(svc.export_xlsx(procode, iso_date(date_from), iso_date(date_to)),
                    media_type=XLSX,
                    headers={"Content-Disposition": f"attachment; filename={procode}_levels.xlsx"})


# ---- Writes --------------------------------------------------------------------

@router.post("")
def post_benchmark(body: BenchmarkIn) -> dict:
    """Creates a benchmark (entity + level series + portfolio) or renames it,
    and assigns it to a house fund when `fund` is given."""
    try:
        return svc.create(body.procode, body.display_name, body.base_currency, body.fund)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/series")
def post_series(body: SeriesIn) -> dict:
    """Registers a Bloomberg ticker as a priced series, 'backfill-pending'.
    Writes the registry only; the Bloomberg machine fetches the history."""
    try:
        return svc.register_series(body.model_dump())
    except SeriesConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/{procode}/versions")
def post_version(procode: str, body: VersionIn) -> dict:
    """Appends one definition version. Never edits or deletes an existing one."""
    try:
        version_id = svc.add_version(procode, body.model_dump())
    except LookupError:
        raise _not_found(procode) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, "version_id": version_id}


@router.delete("/{procode}")
def delete_benchmark(procode: str) -> dict:
    """Removes a benchmark with its versions, levels and fund link. The
    dashboard asks the operator to confirm before calling this; the API
    itself has no undo. Refused while the background task is busy, so a
    rebuild never writes levels of an index that is being removed."""
    if task.status().get("active"):
        raise HTTPException(
            status_code=409,
            detail="A task is running; wait for it to finish before removing a benchmark.")
    try:
        result = svc.delete(procode)
    except LookupError:
        raise _not_found(procode) from None
    return {"ok": True, **result}


@router.post("/{procode}/materialize")
def post_materialize(procode: str) -> dict:
    """Regenerates the levels, valuation and holdings in the background task."""
    bench = svc.get(procode)
    if bench is None:
        raise _not_found(procode)
    if not bench["versions"]:
        raise HTTPException(
            status_code=400,
            detail=f"{bench['display_name']} has no version yet: "
                   "declare its basket before rebuilding.")
    action = f"rebuild of {bench['display_name']}"
    try:
        task.launch(action, task.with_log(svc.rebuild, procode=procode))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"ok": True, "action": action}
