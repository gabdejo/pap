-- 52_bloomberg_dato.sql
-- ---------------------------------------------------------------
-- Data points for the manual Bloomberg series registry. Long
-- format, one row per (serie, fecha): adding a ticker is an INSERT,
-- not a schema change, and mixed frequencies need no special case
-- (a quarterly series is simply a series with fewer rows).
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bloomberg_dato (
    serie_id       INTEGER NOT NULL REFERENCES bloomberg_serie (serie_id),
    fecha          DATE NOT NULL,
    -- 10 decimals: covers prices, rates and ratios without rounding
    valor          NUMERIC(24, 10) NOT NULL,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (serie_id, fecha)
);

CREATE INDEX IF NOT EXISTS idx_bloomberg_dato_fecha
    ON bloomberg_dato (fecha);
