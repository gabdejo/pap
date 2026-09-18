
# web/api/main.py
# ---------------------------------------------------------------------------
# FastAPI app for the analytics dashboards.
# ---------------------------------------------------------------------------
# Serves BOTH the JSON API (/api/...) and the built Next.js static bundle on
# the same origin -- relative /api URLs in the front end then need no CORS.
#
# Run (dev):  uvicorn web.api.main:app --reload --port 8000   (repo root on PYTHONPATH)
# The Next.js dev server (port 3000) proxies /api here via next.config rewrites.
# For an integrated test, build the bundle (npm run build) so out/ exists and is
# served from / below.
# ---------------------------------------------------------------------------
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from web.api.routes import (contribution, formatos, portfolios, positions,
                            prices, tradebook,
                            securities, spp, spp_bloomberg, spp_carga)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Converges the database on startup: applies the schema files, then
    registers the SPP series.

    Both steps are idempotent (CREATE TABLE IF NOT EXISTS, plus the
    convergence ALTERs in 56_spp_migraciones.sql, and 51 upserts), and
    both exist for the machine that installs this project from a zip:
    updating means unzipping a newer copy, with no migration step a
    person could forget. Without the registration, a freshly created
    database yields a tablero that opens and reads empty but refuses
    every write with "no hay serie registrada - corre
    run_sbs_valor_cuota.py --solo-registro", a step nothing in the UI
    mentions.

    A failure here must NEVER stop the API from starting: the tablero
    is also how the operator finds out what is wrong, and an API that
    refuses to boot can only say it through a console they may not be
    looking at.
    """
    try:
        from src.db.bootstrap import create_schema
        from src.db.connection import get_connection
        with get_connection() as conn:
            create_schema(conn)
    except Exception as exc:
        logger.warning(f"schema check skipped at startup: {exc}")
    try:
        from src.pipelines.prices.sbs.valor_cuota.run import ensure_registered
        ensure_registered()
    except Exception as exc:
        logger.warning(f"SPP series registration skipped at startup: {exc}")
    yield


app = FastAPI(title="Portfolio Analytics API", version="0.1.0", lifespan=lifespan)

# Dev convenience: allow the Next dev server (localhost:3000) to call the API
# directly. In production the bundle is same-origin, so this is harmless.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(securities.router)
app.include_router(prices.router)
app.include_router(portfolios.router)
app.include_router(positions.router)
app.include_router(contribution.router)
app.include_router(spp.router)
app.include_router(spp_bloomberg.router)
app.include_router(spp_carga.router)
app.include_router(tradebook.router)
app.include_router(formatos.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


class Bundle(StaticFiles):
    """
    StaticFiles that states a cache policy instead of leaving it to the
    browser's guess.

    Starlette sends only last-modified/etag, so browsers apply heuristic
    freshness (a fraction of the file's age) and serve a CACHED
    index.html without revalidating - which is why every rebuild used to
    need a Ctrl+F5 to be seen. The split is the one the bundle's own
    naming makes safe: /_next/static/* filenames carry a content hash
    and can be cached forever, while the HTML entry points must be
    revalidated on every navigation.
    """

    def file_response(self, *args, **kwargs):
        respuesta = super().file_response(*args, **kwargs)
        ruta = str(getattr(respuesta, "path", "")).replace("\\", "/")
        inmutable = "/_next/static/" in ruta
        respuesta.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if inmutable
            else "no-cache")
        return respuesta


# Serve the built Next.js static export from / when it exists (post-build).
_BUNDLE = Path(__file__).resolve().parents[1] / "apps" / "dashboards" / "out"
if _BUNDLE.is_dir():
    app.mount("/", Bundle(directory=str(_BUNDLE), html=True), name="dashboards")
    logger.info(f"serving static bundle from {_BUNDLE}")
else:
    logger.info(f"no static bundle at {_BUNDLE} (dev mode -- use the Next dev server)")

