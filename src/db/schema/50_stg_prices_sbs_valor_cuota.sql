-- 50_stg_prices_sbs_valor_cuota.sql
-- ---------------------------------------------------------------
-- NOTE on numbering: the 50+ block is reserved for the tables that
-- came from the SPP monitor integration (50-52 today). Upstream
-- pipelines keep numbering from 36 upward, so the two lines of work
-- can add tables without ever colliding on a slot again.
-- ---------------------------------------------------------------
-- Staging table for the SPP daily "valor cuota" feed (SBS variables
-- page scrape + monthly historical XLS).
--
-- LONG format, one row per (afp, fondo, date, load): the standalone
-- monitor stored this as a 48-column wide table, but here the fact
-- destination is fact_prices (long), so staging mirrors that grain.
-- The three SBS metrics travel together because the daily page
-- publishes them side by side; the historical XLS only carries
-- valor_cuota and leaves the other two NULL.
--
-- Loaded by src/pipelines/prices/sbs/valor_cuota/extract.py.
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_prices_sbs_valor_cuota (
    -- Stable AFP identity (registry clave, e.g. 'habitat') + fund type
    afp              TEXT NOT NULL,
    fondo            INTEGER NOT NULL,

    -- The three metrics the SBS publishes per AFP x fund
    valor_cuota      DOUBLE PRECISION,
    cuotas           DOUBLE PRECISION,
    fondo_soles      DOUBLE PRECISION,

    -- Where this row came from: 'extraccion' (daily page) | 'historico' (XLS)
    fuente           TEXT,

    -- Pipeline metadata
    date             DATE NOT NULL,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (afp, fondo, date, loaded_at)
);

CREATE INDEX IF NOT EXISTS idx_stg_sbs_valor_cuota_date
    ON stg_prices_sbs_valor_cuota (date);
