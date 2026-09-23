# src/scrapers/spp.py
# ---------------------------------------------------------------
# Acquisition for the SPP valor cuota feed (www.sbs.gob.pe).
#
# The SBS main site sits behind the Imperva/Incapsula WAF: requests,
# curl and headless browsers all receive a block page. The only path
# through is REAL Chrome, visible, with a persistent profile that
# keeps the WAF cookie between runs - which is why this scraper uses
# Playwright (channel='chrome', headless=False) instead of the
# Selenium+chromedriver stack the other scrapers use, and why the
# scheduled task that drives it must run in an interactive session.
#
# Acquisition only: HTML/bytes in, files under data/raw/spp/ as a
# trail. Parsing lives in the pipeline's extract.py.
# ---------------------------------------------------------------

import logging
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from src.shared.paths import DATA_DIR, RAW_DIR

logger = logging.getLogger(__name__)

URL_DIARIO = "https://www.sbs.gob.pe/app/spp/variablesSPP_net/PagSS/variables_spp.aspx"
URL_INDICE_HISTORICO = ("https://www.sbs.gob.pe/app/stats/"
                        "EstadisticaSistemaFinancieroResultadosHist.asp?c=FP-130706&Y=0")
TEXTO_ENLACE_HISTORICO = "Valores cuota (desde"

SELECTOR_TABLA_DIARIA = "table.APLI_tabla2"

# Persistent Chrome profile: holds the Imperva cookie between runs.
PROFILE_DIR = DATA_DIR / "browser_profile_spp"
SPP_RAW_DIR = RAW_DIR / "spp"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class FaltaChrome(RuntimeError):
    """Google Chrome is not installed - retrying cannot help."""


# Playwright phrasings for "the browser binary is not there". Retrying
# these wastes three window launches and 12 seconds of the scheduled
# task's budget before reporting a cause that will not change.
_SIN_CHROME = ("is not found", "executable doesn't exist", "playwright install")


def _sin_reintento(exc: Exception) -> bool:
    texto = str(exc).lower()
    return any(marca in texto for marca in _SIN_CHROME)


def _guardar_fallo(html: str, intento: int) -> Path:
    """Keeps the page that did not parse, for the post mortem."""
    SPP_RAW_DIR.mkdir(parents=True, exist_ok=True)
    destino = SPP_RAW_DIR / f"fallo_{date.today():%Y%m%d}_{intento}.html"
    try:
        destino.write_text(html, encoding="utf-8")
    except Exception:
        pass
    return destino


def fetch_html(url: str, wait_selector: str | None = None,
               retries: int = 3) -> str:
    """
    Downloads a page from www.sbs.gob.pe through the WAF.

    Playwright is imported inside the function so machines without it
    (office installs, CI) can still import the module; only actually
    scraping requires the dependency.

    Failures are told apart instead of collapsed into one timeout: a
    missing Chrome fails immediately (no retry can install it), a WAF
    challenge says so and names the fix (open Chrome by hand and solve
    it once), and a page whose expected table never appeared is saved
    to data/raw/spp/ so a change on the SBS side can be seen.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    last_error = None
    for attempt in range(1, retries + 1):
        p = ctx = None
        try:
            p = sync_playwright().start()
            try:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE_DIR), channel="chrome",
                    headless=False, locale="es-PE", timezone_id="America/Lima",
                    viewport={"width": 1440, "height": 1000},
                    args=["--disable-blink-features=AutomationControlled"])
            except Exception as exc:
                if _sin_reintento(exc):
                    raise FaltaChrome(
                        "Google Chrome no esta instalado en esta maquina. El "
                        "WAF de la SBS exige Chrome real (el Chromium de "
                        "Playwright no sirve): instala Google Chrome y vuelve "
                        f"a correr. Detalle: {str(exc).splitlines()[0][:160]}")
                raise
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=45_000)
                except PlaywrightTimeout:
                    # The WAF check below never ran on the daily path
                    # before this: a challenge page has no table either,
                    # so every block was reported as a bare timeout.
                    html = page.content()
                    copia = _guardar_fallo(html, attempt)
                    if "_Incapsula_Resource" in html or "Incapsula" in html:
                        raise RuntimeError(
                            "El WAF de la SBS bloqueo la peticion (pagina de "
                            "verificacion). Abre Chrome a mano, entra a "
                            f"{url}, resuelve el reto una vez y vuelve a "
                            "correr: la cookie queda en el perfil.")
                    raise RuntimeError(
                        f"La tabla '{wait_selector}' no aparecio en la pagina "
                        "de la SBS; puede haber cambiado el formato. Copia de "
                        f"lo recibido en {copia}")
            else:
                page.wait_for_timeout(4_000)
            html = page.content()
            if "_Incapsula_Resource" in html:
                raise RuntimeError(
                    "El WAF de la SBS bloqueo la peticion. Abre Chrome a mano, "
                    f"entra a {url} y resuelve el reto una vez.")
            return html
        except FaltaChrome:
            raise
        except Exception as exc:
            last_error = exc
            logger.warning(f"spp fetch attempt {attempt}/{retries} failed: "
                           f"{str(exc).splitlines()[0][:120]}")
            if attempt < retries:
                time.sleep(4 * attempt)
        finally:
            for closer in (getattr(ctx, "close", None), getattr(p, "stop", None)):
                if closer:
                    try:
                        closer()
                    except Exception:
                        pass
    raise RuntimeError(f"No se pudo abrir {url}: {last_error}")


def fetch_daily_html(run_date: date | None = None) -> str:
    """
    The SPP variables page (last 7 business days, three metrics per
    AFP x fund). Saves a raw copy under data/raw/spp/ as the trail.
    """
    logger.info("Abriendo la pagina diaria de variables SPP...")
    html = fetch_html(URL_DIARIO, wait_selector=SELECTOR_TABLA_DIARIA)
    stamp = (run_date or date.today()).strftime("%Y%m%d")
    SPP_RAW_DIR.mkdir(parents=True, exist_ok=True)
    (SPP_RAW_DIR / f"variables_spp_{stamp}.html").write_text(html, encoding="utf-8")
    return html


def download_historic_xls() -> Path:
    """
    Resolves and downloads the SBS monthly historical XLS (valor cuota
    since Aug 1993). Only the index page needs the WAF-piercing Chrome;
    the file link itself downloads over plain requests.
    """
    from bs4 import BeautifulSoup

    logger.info("Resolviendo el enlace del XLS historico...")
    html = fetch_html(URL_INDICE_HISTORICO)
    url = None
    for a in BeautifulSoup(html, "lxml").find_all("a"):
        if " ".join(a.get_text(" ").split()).lower().startswith(
                TEXTO_ENLACE_HISTORICO.lower()):
            url = a.get("href")
            break
    if not url:
        raise RuntimeError("No se encontro el enlace del XLS historico.")

    # The href may come relative (usual in ASP pages) and may carry a query
    # string: resolve it against the index URL and name the file from the
    # path only - '?' is not a legal filename character on Windows.
    url = urljoin(URL_INDICE_HISTORICO, url)
    nombre = Path(urlsplit(url).path).name or "valores_cuota.xls"
    logger.info(f"Descargando {nombre}")
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=180)
    r.raise_for_status()
    SPP_RAW_DIR.mkdir(parents=True, exist_ok=True)
    destino = SPP_RAW_DIR / nombre
    destino.write_bytes(r.content)
    logger.info(f"Descargados {len(r.content):,} bytes -> {destino}")
    return destino
