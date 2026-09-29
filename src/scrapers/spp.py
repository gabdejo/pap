# src/scrapers/spp.py
# ---------------------------------------------------------------
# Acquisition for the SPP valor cuota feed (www.sbs.gob.pe).
#
# The SBS main site sits behind the Imperva/Incapsula WAF: plain HTTP
# clients, curl and headless browsers all receive a block page. The
# only path through is REAL Chrome, visible, with a persistent profile
# that keeps the WAF cookie between runs - which is why this scraper
# uses Playwright (channel='chrome', headless=False) instead of the
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
from urllib.request import Request, urlopen

from src.shared.paths import DATA_DIR, RAW_DIR

logger = logging.getLogger(__name__)

DAILY_URL = "https://www.sbs.gob.pe/app/spp/variablesSPP_net/PagSS/variables_spp.aspx"
HISTORY_INDEX_URL = ("https://www.sbs.gob.pe/app/stats/"
                     "EstadisticaSistemaFinancieroResultadosHist.asp?c=FP-130706&Y=0")
HISTORY_LINK_TEXT = "Valores cuota (desde"

DAILY_TABLE_SELECTOR = "table.APLI_tabla2"

# Persistent Chrome profile: holds the Imperva cookie between runs.
PROFILE_DIR = DATA_DIR / "browser_profile_spp"
SPP_RAW_DIR = RAW_DIR / "spp"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class ChromeMissing(RuntimeError):
    """Google Chrome is not installed - retrying cannot help."""


# Playwright phrasings for "the browser binary is not there". Retrying
# these wastes three window launches and 12 seconds of the scheduled
# task's budget before reporting a cause that will not change.
_NO_CHROME = ("is not found", "executable doesn't exist", "playwright install")


def _no_retry(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(mark in text for mark in _NO_CHROME)


def _save_failure(html: str, attempt: int) -> Path:
    """Keeps the page that did not parse, for the post mortem."""
    SPP_RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = SPP_RAW_DIR / f"failure_{date.today():%Y%m%d}_{attempt}.html"
    try:
        target.write_text(html, encoding="utf-8")
    except OSError:
        pass
    return target


def fetch_html(url: str, wait_selector: str | None = None,
               retries: int = 3) -> str:
    """
    Downloads a page from www.sbs.gob.pe through the WAF.

    Playwright is imported inside the function so machines without it
    can still import the module; only actually scraping requires it.

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
                if _no_retry(exc):
                    raise ChromeMissing(
                        "Google Chrome is not installed on this machine. The SBS "
                        "WAF requires real Chrome (Playwright's Chromium is "
                        "blocked): install Google Chrome and run again. Detail: "
                        f"{str(exc).splitlines()[0][:160]}")
                raise
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=45_000)
                except PlaywrightTimeout:
                    # A challenge page has no table either, so without
                    # this check every block was reported as a timeout.
                    html = page.content()
                    copy = _save_failure(html, attempt)
                    if "_Incapsula_Resource" in html or "Incapsula" in html:
                        raise RuntimeError(
                            "The SBS WAF blocked the request (challenge page). "
                            f"Open Chrome by hand, visit {url}, solve the "
                            "challenge once and run again: the cookie stays in "
                            "the profile.")
                    raise RuntimeError(
                        f"The table '{wait_selector}' never appeared in the SBS "
                        "page; the layout may have changed. A copy of what was "
                        f"received is at {copy}")
            else:
                page.wait_for_timeout(4_000)
            html = page.content()
            if "_Incapsula_Resource" in html:
                raise RuntimeError(
                    "The SBS WAF blocked the request. Open Chrome by hand, visit "
                    f"{url} and solve the challenge once.")
            return html
        except ChromeMissing:
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
    raise RuntimeError(f"Could not open {url}: {last_error}")


def fetch_daily_html(run_date: date | None = None) -> str:
    """
    The SPP variables page (last 7 business days, three metrics per
    AFP x fund). Saves a raw copy under data/raw/spp/ as the trail.
    """
    logger.info("Opening the SPP daily variables page...")
    html = fetch_html(DAILY_URL, wait_selector=DAILY_TABLE_SELECTOR)
    stamp = (run_date or date.today()).strftime("%Y%m%d")
    SPP_RAW_DIR.mkdir(parents=True, exist_ok=True)
    (SPP_RAW_DIR / f"variables_spp_{stamp}.html").write_text(html, encoding="utf-8")
    return html


def download_history_xls() -> Path:
    """
    Resolves and downloads the SBS monthly historical XLS (valor cuota
    since Aug 1993). Only the index page needs the WAF-piercing Chrome;
    the file link itself downloads over a plain HTTP request.
    """
    from bs4 import BeautifulSoup

    logger.info("Resolving the history XLS link...")
    html = fetch_html(HISTORY_INDEX_URL)
    url = None
    for a in BeautifulSoup(html, "html.parser").find_all("a"):
        if " ".join(a.get_text(" ").split()).lower().startswith(HISTORY_LINK_TEXT.lower()):
            url = a.get("href")
            break
    if not url:
        raise RuntimeError("The history XLS link was not found in the SBS index page.")

    # The href may come relative (usual in ASP pages) and may carry a query
    # string: resolve it against the index URL and name the file from the
    # path only - '?' is not a legal filename character on Windows.
    url = urljoin(HISTORY_INDEX_URL, url)
    name = Path(urlsplit(url).path).name or "valores_cuota.xls"
    logger.info(f"Downloading {name}")
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=180) as resp:
        content = resp.read()
    SPP_RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = SPP_RAW_DIR / name
    target.write_bytes(content)
    logger.info(f"Downloaded {len(content):,} bytes -> {target}")
    return target
