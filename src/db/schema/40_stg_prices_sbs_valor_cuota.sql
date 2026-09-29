-- 40_stg_prices_sbs_valor_cuota.sql
-- ---------------------------------------------------------------
-- Staging table for the SPP "valor cuota" feed: the SBS variables
-- page scraped daily (last 7 business days, three metrics per AFP x
-- fund) and the monthly historical XLS (valor cuota since 1993).
--
-- LONG format, one row per (afp, fondo, date, load): the standalone
-- monitor stored this as a 48-column wide table, but here the fact
-- destination is fact_prices (long), so staging mirrors that grain.
-- The three SBS metrics travel together because the daily page
-- publishes them side by side; the historical XLS only carries
-- valor_cuota and leaves the other two NULL.
--
-- Column names valor_cuota / cuotas / fondo_soles / fondo are the
-- SBS's own names for the published figures and stay as they come.
--
-- Rows are never updated: every load appends its own copy, keyed by
-- loaded_at, so a restatement is visible as two rows. The facts are
-- derived by src/pipelines/prices/sbs/valor_cuota/transform.py.
-- Loaded by src/pipelines/prices/sbs/valor_cuota/extract.py.
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_prices_sbs_valor_cuota (
    -- Stable AFP identity (registry key, e.g. 'habitat') + fund type
    afp              TEXT NOT NULL,
    fondo            INTEGER NOT NULL,

    -- The three metrics the SBS publishes per AFP x fund
    valor_cuota      DOUBLE PRECISION,
    cuotas           DOUBLE PRECISION,
    fondo_soles      DOUBLE PRECISION,

    -- Where this row came from: 'daily_page' | 'history_xls'
    source           TEXT,

    -- Pipeline metadata
    date             DATE NOT NULL,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (afp, fondo, date, loaded_at)
);

CREATE INDEX IF NOT EXISTS idx_stg_prices_sbs_valor_cuota_date
    ON stg_prices_sbs_valor_cuota (date);
