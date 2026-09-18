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
# accumulated forward from daily extractions.
#
# AFPs are resolved by alias (clave_de), never by text equality, so
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

from src.pipelines.prices.sbs.valor_cuota.afps import clave_de, opera
from src.shared import tabular

logger = logging.getLogger(__name__)

RE_FECHA = re.compile(r"(\d{2}/\d{2}/\d{4})")
RE_FONDO = re.compile(r"Fondo\s*(?:Tipo\s*)?(\d+)", re.IGNORECASE)

# Column order of each fund block in the daily page.
METRICAS_PAGINA = ["cuotas", "fondo_soles", "valor_cuota"]

HOJA_HISTORICO = "Valor cuota diario"

STG_COLUMNS = ["afp", "fondo", "valor_cuota", "cuotas", "fondo_soles",
               "fuente", "date", "loaded_at"]


def _num(valor):
    """SBS cell -> float|None. Delegates to the shared tolerant parser
    (typed passthrough, NaN guard, null tokens, thousands commas); only
    the '%' strip is local because the shared reader never sees one."""
    if isinstance(valor, str):
        valor = valor.replace("%", "")
    return tabular.num_flexible(valor, coma_decimal=False)


def parse_daily(html: str, no_registradas: set | None = None) -> pd.DataFrame:
    """
    Long DataFrame [afp, fondo, valor_cuota, cuotas, fondo_soles, date]
    from the SPP variables page (last 7 business days).

    `no_registradas` (optional) accumulates AFP names seen in the page
    that config/afps.yaml does not know - the review report shows them
    so a new AFP is never silently dropped.
    """
    sopa = BeautifulSoup(html, "lxml")
    tablas = sopa.select("table.APLI_tabla2")
    if not tablas:
        raise ValueError("No se hallaron las tablas de la pagina diaria.")

    def celdas(fila):
        return [" ".join(c.get_text(" ").split()) for c in fila.find_all(["td", "th"])]

    if no_registradas is None:
        no_registradas = set()
    filas: list[dict] = []
    for tabla in tablas:
        tr = tabla.find_all("tr")
        if len(tr) < 4:
            continue
        m = RE_FECHA.search(" ".join(celdas(tr[0])))
        if not m:
            continue
        fecha = dt.datetime.strptime(m.group(1), "%d/%m/%Y").date()
        fondos_pagina = [int(mf.group(1)) for c in celdas(tr[1])
                         for mf in [RE_FONDO.search(c)] if mf]
        if not fondos_pagina:
            continue
        for fila in tr[3:]:
            cs = celdas(fila)
            if len(cs) < 1 + len(fondos_pagina) * len(METRICAS_PAGINA):
                continue
            afp_texto = cs[0].strip()
            clave = clave_de(afp_texto)
            if not clave:
                if afp_texto and not afp_texto.isdigit():
                    no_registradas.add(afp_texto.upper())
                continue
            for i, fondo in enumerate(fondos_pagina):
                bloque = cs[1:][i * len(METRICAS_PAGINA):(i + 1) * len(METRICAS_PAGINA)]
                if len(bloque) != len(METRICAS_PAGINA) or not opera(clave, fondo):
                    continue
                reg = {"afp": clave, "fondo": fondo, "date": fecha}
                for j, metrica in enumerate(METRICAS_PAGINA):
                    reg[metrica] = _num(bloque[j])
                if any(reg.get(m) is not None for m in METRICAS_PAGINA):
                    filas.append(reg)

    if no_registradas:
        logger.warning(
            "La SBS publico datos de AFP no registradas: %s. Sus cifras NO se "
            "estan guardando: agregalas en config/afps.yaml.",
            ", ".join(sorted(no_registradas)))
    if not filas:
        raise ValueError("No se extrajo ninguna fecha de la pagina diaria.")

    df = pd.DataFrame(filas)
    logger.info(f"Pagina diaria: {df['date'].nunique()} fechas "
                f"({df['date'].min()} a {df['date'].max()}) - {len(df)} filas.")
    return df


def parse_historico(contenido: bytes,
                    no_registradas: set | None = None) -> pd.DataFrame:
    """
    Long DataFrame [afp, fondo, valor_cuota, date] from the SBS monthly
    XLS (since Aug 1993). The file is named .xls but is xlsx inside, so
    it is read by content, not extension. `no_registradas` accumulates
    unknown AFP names for the review report.
    """
    try:
        crudo = pd.read_excel(io.BytesIO(contenido), sheet_name=HOJA_HISTORICO,
                              header=None, engine="openpyxl")
    except Exception as exc:
        try:
            hojas = pd.ExcelFile(io.BytesIO(contenido), engine="openpyxl").sheet_names
        except Exception:
            raise ValueError(
                "No se pudo abrir el archivo como Excel. Debe ser el archivo de "
                "valores cuota diarios de la SBS tal como se descarga. Si es un "
                ".xls antiguo, abrelo en Excel y guardalo como .xlsx.") from exc
        raise ValueError(
            f"El archivo no tiene la hoja '{HOJA_HISTORICO}'. Tiene: "
            f"{', '.join(hojas)}.") from exc

    grupos = crudo.iloc[2].ffill()   # row 3: 'Fondo Tipo 0..3' (merged cells)
    cabecera_afps = crudo.iloc[3]    # row 4: AFP inside each group

    mapa: dict[int, tuple[str, int]] = {}
    if no_registradas is None:
        no_registradas = set()
    for i in range(1, crudo.shape[1]):
        m = RE_FONDO.search(str(grupos.iloc[i] or ""))
        bruto = str(cabecera_afps.iloc[i] or "").strip()
        clave = clave_de(bruto)
        if m and clave and opera(clave, int(m.group(1))):
            mapa[i] = (clave, int(m.group(1)))
        elif m and bruto and not clave:
            no_registradas.add(bruto.upper())
    if no_registradas:
        logger.warning("XLS historico: AFP no registradas ignoradas: %s",
                       ", ".join(sorted(no_registradas)))
    if not mapa:
        raise ValueError("No se pudo mapear las columnas del XLS.")

    filas = []
    for _, fila in crudo.iloc[4:].iterrows():
        # dayfirst: the SBS file carries typed dates, but a re-saved or
        # hand-edited workbook can bring them as dd/mm TEXT - without this,
        # days <= 12 silently parse month-first and land on wrong dates.
        fecha = pd.to_datetime(fila.iloc[0], errors="coerce", dayfirst=True)
        if pd.isna(fecha):
            continue                     # footnotes and blank rows
        for i, (clave, fondo) in mapa.items():
            v = _num(fila.iloc[i])
            if v is not None:
                filas.append({"afp": clave, "fondo": fondo,
                              "valor_cuota": v, "date": fecha.date()})

    if not filas:
        raise ValueError("El XLS historico no trajo ningun valor cuota.")
    df = pd.DataFrame(filas)
    logger.info(f"Historico: {df['date'].nunique()} fechas "
                f"({df['date'].min()} a {df['date'].max()}) - solo valor cuota.")
    return df


def prepare_stg(df: pd.DataFrame, fuente: str) -> pd.DataFrame:
    """Fills the staging metadata columns on a parsed DataFrame."""
    if df.empty:
        return pd.DataFrame(columns=STG_COLUMNS)
    out = df.copy()
    for col in ("valor_cuota", "cuotas", "fondo_soles"):
        if col not in out.columns:
            out[col] = None
    out["fuente"] = fuente
    out["loaded_at"] = dt.datetime.now()
    return out[STG_COLUMNS]


STG_INSERT = """
    INSERT INTO stg_prices_sbs_valor_cuota (
        afp, fondo, valor_cuota, cuotas, fondo_soles,
        fuente, date, loaded_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (afp, fondo, date, loaded_at) DO NOTHING
"""

# Same batching threshold rationale as loader.py: the daily scrape (~100
# rows) keeps the per-row convention, the historical XLS (~100k rows)
# goes through executemany in one round trip.
LOTE_MINIMO = 1000


def load_stg(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    params = [
        (row["afp"], int(row["fondo"]),
         _f(row.get("valor_cuota")), _f(row.get("cuotas")),
         _f(row.get("fondo_soles")),
         row.get("fuente"), row["date"], row["loaded_at"])
        for _, row in df.iterrows()
    ]
    if len(params) >= LOTE_MINIMO:
        cur = conn.cursor()
        cur.executemany(STG_INSERT, params)
        inserted = cur.rowcount if cur.rowcount >= 0 else 0
    else:
        inserted = 0
        for p in params:
            cur = conn.execute(STG_INSERT, p)
            if cur.rowcount > 0:
                inserted += 1
    logger.info(f"stg_prices_sbs_valor_cuota: {inserted} rows staged.")
    return inserted


def _f(val):
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return float(val)
