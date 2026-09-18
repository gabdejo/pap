-- 53_benchmark_composicion.sql
-- ---------------------------------------------------------------
-- Composition of the SPP composite indices per fund type: a basket
-- of priced series with weights, VERSIONED by effective date.
--
-- There are TWO indices per fund, told apart by `tipo`:
--   'target'     what the fund is steered against (SPP_TARGET_F{n})
--   'benchmark'  what it is measured against   (SPP_BENCH_F{n})
-- Same machinery, same tables, different rows. The table keeps the
-- name it was born with, when there was only one index and it was
-- called the benchmark: renaming a table reaches every store that
-- references it, for no gain a comment cannot give.
--
-- A rebalance never edits rows - it inserts the full new basket
-- under a new vigente_desde. The composition that governed any past
-- date is therefore always reproducible, and the level series in
-- fact_prices can be regenerated from scratch at any time by the
-- calculation in src/pipelines/prices/sbs/valor_cuota/
-- benchmark_composicion.py.
--
-- Components price from any of THREE stores (fuente):
--   'bloomberg' -> bloomberg_serie/bloomberg_dato (BBG registry)
--   'fact'      -> series_registry/fact_prices (pipeline spine)
--   'manual'    -> serie_manual/serie_manual_dato (keyed-in data
--                  for components no vendor provides)
-- fx_* optionally names a second priced series whose level multiplies
-- the component's (currency conversion); NULL means no conversion.
--
-- Weights are stored normalized to sum 1 per (tipo, fondo,
-- vigente_desde); the writer validates and normalizes (accepts
-- 100-based input). Between rebalances weights DRIFT with prices
-- (buy-and-hold): the stored peso is the weight AT the rebalance
-- date only.
--
-- Shape changes after the first deployment are re-stated in
-- 56_spp_migraciones.sql: CREATE TABLE IF NOT EXISTS never alters
-- what already exists.
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS benchmark_composicion (
    tipo             TEXT NOT NULL DEFAULT 'target'
        CONSTRAINT ck_benchmark_composicion_tipo
        CHECK (tipo IN ('target', 'benchmark')),
    fondo            INTEGER NOT NULL,
    vigente_desde    DATE NOT NULL,

    -- What the component is (display) and where it prices from.
    etiqueta         TEXT NOT NULL,
    fuente           TEXT NOT NULL
        CONSTRAINT ck_benchmark_composicion_fuente
        CHECK (fuente IN ('bloomberg', 'fact', 'manual')),
    ref_id           INTEGER NOT NULL,

    -- Weight at the rebalance date, normalized to sum 1 per basket
    peso             NUMERIC(9, 6) NOT NULL CHECK (peso > 0),

    -- Optional FX leg: component price is multiplied by this series
    fx_fuente        TEXT
        CONSTRAINT ck_benchmark_composicion_fx_fuente
        CHECK (fx_fuente IN ('bloomberg', 'fact', 'manual')),
    fx_ref_id        INTEGER,

    creado_en        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (tipo, fondo, vigente_desde, fuente, ref_id),
    CHECK ((fx_fuente IS NULL) = (fx_ref_id IS NULL))
);

-- The (tipo, fondo, vigente_desde) index lives in 56_spp_migraciones.sql,
-- not here. On a database that already has the table, the CREATE above
-- does nothing - but a CREATE INDEX here WOULD run, on a column that
-- database does not have yet, and create_schema stops at the first file
-- that fails: the very migration that adds the column never runs.
