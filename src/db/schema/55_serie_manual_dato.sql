-- 55_serie_manual_dato.sql
-- ---------------------------------------------------------------
-- Data points of the manual series. Long format, one row per
-- (serie, fecha); re-entering a date replaces its value (the editor
-- upserts), deleting is per-point from the same editor.
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS serie_manual_dato (
    serie_id       INTEGER NOT NULL REFERENCES serie_manual (serie_id),
    fecha          DATE NOT NULL,
    valor          NUMERIC(24, 10) NOT NULL,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (serie_id, fecha)
);
