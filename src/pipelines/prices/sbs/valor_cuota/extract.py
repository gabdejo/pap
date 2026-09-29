# src/pipelines/prices/sbs/valor_cuota/extract.py
# ---------------------------------------------------------------
# Parsers for the two SPP valor cuota sources, plus the staging
# loader. Acquisition (Chrome/WAF, downloads) lives in
# src/scrapers/spp.py; this module turns raw HTML/XLS into the
# staging grain: one row per (afp, fondo, date).
#
# Daily page: each fund type contributes three consecutive columns
# per AFP row - cuotas, fondo, valor_cuota, in that order.
# Historical XLS: valor_cuota only; the SBS does not publish daily
# history for cuotas or fund value, so those stay NULL and are
# accumulated forward from daily scrapes.
#
# AFPs are resolved by alias (key_of), never by text equality, so
# accents, the 'AFP ' prefix and pre-rename names all keep working.
# Unrecognized AFP names are logged loudly: a new AFP silently
# dropped is the worst failure mode this feed can have.
# ---------------------------------------------------------------

import datetime as dt
import io
import logging
import re

import pandas as pd
from bs4 import BeautifulSoup

from src.pipelines.prices.sbs.valor_cuota.afps import key_of, operates
from src.shared import tabular

logger = logging.getLogger(__name__)

RE_DATE = re.compile(r"(\d{2}/\d{2}/\d{4})")
RE_FUND = re.compile(r"Fondo\s*(?:Tipo\s*)?(\d+)", re.IGNORECASE)

# Column order of each fund block in the daily page.
PAGE_METRICS = ["cuotas", "fondo_soles", "valor_cuota"]

HISTORY_SHEET = "Valor cuota diario"

# Staging `source` values: which of the two SBS publications a row came from.
SOURCE_DAILY = "daily_page"
SOURCE_HISTORY = "history_xls"

STG_COLUMNS = ["afp", "fondo", "valor_cuota", "cuotas", "fondo_soles",
               "source", "date", "loaded_at"]


def _num(value) -> float | None:
    """SBS cell -> float|None. Delegates to the shared tolerant parser
    (typed passthrough, NaN guard, null tokens, thousands commas); only
    the '%' strip is local because the shared reader never sees one."""
    if isinstance(value, str):
        value = value.replace("%", "")
    return tabular.parse_number(value, comma_decimal=False)


def parse_daily(html: str, unknown_afps: set | None = None) -> pd.DataFrame:
    """
    Long DataFrame [afp, fondo, valor_cuota, cuotas, fondo_soles, date]
    from the SPP variables page (last 7 business days).

    `unknown_afps` (optional) accumulates AFP names seen in the page
    that config/afps.yaml does not know - the review report shows them
    so a new AFP is never silently dropped.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.select("table.APLI_tabla2")
    if not tables:
        raise ValueError("No data tables found in the SBS daily page.")

    def cells(row):
        return [" ".join(c.get_text(" ").split()) for c in row.find_all(["td", "th"])]

    if unknown_afps is None:
        unknown_afps = set()
    rows: list[dict] = []
    for table in tables:
        tr = table.find_all("tr")
        if len(tr) < 4:
            continue
        m = RE_DATE.search(" ".join(cells(tr[0])))
        if not m:
            continue
        day = dt.datetime.strptime(m.group(1), "%d/%m/%Y").date()
        page_funds = [int(mf.group(1)) for c in cells(tr[1])
                      for mf in [RE_FUND.search(c)] if mf]
        if not page_funds:
            continue
        for row in tr[3:]:
            cs = cells(row)
            if len(cs) < 1 + len(page_funds) * len(PAGE_METRICS):
                continue
            afp_text = cs[0].strip()
            key = key_of(afp_text)
            if not key:
                if afp_text and not afp_text.isdigit():
                    unknown_afps.add(afp_text.upper())
                continue
            for i, fund in enumerate(page_funds):
                block = cs[1:][i * len(PAGE_METRICS):(i + 1) * len(PAGE_METRICS)]
                if len(block) != len(PAGE_METRICS) or not operates(key, fund):
                    continue
                record = {"afp": key, "fondo": fund, "date": day}
                for j, metric in enumerate(PAGE_METRICS):
                    record[metric] = _num(block[j])
                if any(record.get(m) is not None for m in PAGE_METRICS):
                    rows.append(record)

    if unknown_afps:
        logger.warning(
            f"The SBS published figures for AFPs the registry does not know: "
            f"{', '.join(sorted(unknown_afps))}. They are NOT being stored: "
            f"add them to config/afps.yaml.")
    if not rows:
        raise ValueError("No dates could be read from the SBS daily page.")

    df = pd.DataFrame(rows)
    logger.info(f"Daily page: {df['date'].nunique()} dates "
                f"({df['date'].min()} to {df['date'].max()}) - {len(df)} rows.")
    return df


def parse_history(content: bytes,
                  unknown_afps: set | None = None) -> pd.DataFrame:
    """
    Long DataFrame [afp, fondo, valor_cuota, date] from the SBS monthly
    XLS (since Aug 1993). The file is named .xls but is xlsx inside, so
    it is read by content, not extension. `unknown_afps` accumulates
    unknown AFP names for the review report.
    """
    try:
        raw = pd.read_excel(io.BytesIO(content), sheet_name=HISTORY_SHEET,
                            header=None, engine="openpyxl")
    except Exception as exc:
        try:
            sheets = pd.ExcelFile(io.BytesIO(content), engine="openpyxl").sheet_names
        except Exception:
            raise ValueError(
                "The file could not be opened as Excel. It must be the SBS "
                "daily valor cuota file as downloaded. If it is a legacy "
                ".xls, open it in Excel and save it as .xlsx.") from exc
        raise ValueError(
            f"The file has no sheet named '{HISTORY_SHEET}'. It has: "
            f"{', '.join(sheets)}.") from exc

    groups = raw.iloc[2].ffill()   # row 3: 'Fondo Tipo 0..3' (merged cells)
    afp_header = raw.iloc[3]       # row 4: AFP inside each group

    column_map: dict[int, tuple[str, int]] = {}
    if unknown_afps is None:
        unknown_afps = set()
    for i in range(1, raw.shape[1]):
        m = RE_FUND.search(str(groups.iloc[i] or ""))
        text = str(afp_header.iloc[i] or "").strip()
        key = key_of(text)
        if m and key and operates(key, int(m.group(1))):
            column_map[i] = (key, int(m.group(1)))
        elif m and text and not key:
            unknown_afps.add(text.upper())
    if unknown_afps:
        logger.warning(f"History XLS: unregistered AFPs ignored: "
                       f"{', '.join(sorted(unknown_afps))}")
    if not column_map:
        raise ValueError("The history XLS columns could not be mapped to any AFP.")

    rows = []
    for _, row in raw.iloc[4:].iterrows():
        # dayfirst: the SBS file carries typed dates, but a re-saved or
        # hand-edited workbook can bring them as dd/mm TEXT - without this,
        # days <= 12 silently parse month-first and land on wrong dates.
        day = pd.to_datetime(row.iloc[0], errors="coerce", dayfirst=True)
        if pd.isna(day):
            continue                     # footnotes and blank rows
        for i, (key, fund) in column_map.items():
            v = _num(row.iloc[i])
            if v is not None:
                rows.append({"afp": key, "fondo": fund,
                             "valor_cuota": v, "date": day.date()})

    if not rows:
        raise ValueError("The history XLS brought no valor cuota values.")
    df = pd.DataFrame(rows)
    logger.info(f"History XLS: {df['date'].nunique()} dates "
                f"({df['date'].min()} to {df['date'].max()}) - valor cuota only.")
    return df


def prepare_stg(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Fills the staging metadata columns on a parsed DataFrame."""
    if df.empty:
        return pd.DataFrame(columns=STG_COLUMNS)
    out = df.copy()
    for col in ("valor_cuota", "cuotas", "fondo_soles"):
        if col not in out.columns:
            out[col] = None
    out["source"] = source
    out["loaded_at"] = dt.datetime.now()
    return out[STG_COLUMNS]


STG_INSERT = """
    INSERT INTO stg_prices_sbs_valor_cuota (
        afp, fondo, valor_cuota, cuotas, fondo_soles,
        source, date, loaded_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (afp, fondo, date, loaded_at) DO NOTHING
"""


def load_stg(conn, df: pd.DataFrame) -> int:
    """Row-by-row staging insert (project convention: an error names
    the offending row). Returns the number of rows inserted."""
    if df.empty:
        return 0
    cur = conn.cursor()
    inserted = 0
    for _, row in df.iterrows():
        cur.execute(
            STG_INSERT,
            (row["afp"], int(row["fondo"]),
             _float(row.get("valor_cuota")), _float(row.get("cuotas")),
             _float(row.get("fondo_soles")),
             row.get("source"), row["date"], row["loaded_at"]),
        )
        if cur.rowcount > 0:
            inserted += 1
    logger.info(f"stg_prices_sbs_valor_cuota: {inserted} rows staged.")
    return inserted


def _float(val) -> float | None:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return float(val)
