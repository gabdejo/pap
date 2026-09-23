-- 57_benchmark.sql
-- ---------------------------------------------------------------
-- The composite indices of each fund type, as named things.
--
-- Two per fund, told apart by `tipo`: the TARGET the fund is steered
-- against and the BENCHMARK it is measured against. Each has a name
-- of its own ("Renta mixta global 60/40") instead of being known
-- only as SPP_TARGET_F2 or SPP_BENCH_F2. The name is what the
-- tablero shows: in the chart legend, in the book and in the
-- composition editor.
--
-- Declaring an index here is also what ENABLES a fund to have one.
-- Adding a row is enough, and the series gets registered on the
-- spot. config/afps.yaml keeps the default fund list for a fresh
-- database (it applies to both types).
--
-- The level series itself does not move: it is
-- SPP_TARGET_F{fondo} or SPP_BENCH_F{fondo} / PX_LAST / source
-- 'benchmark' in fact_prices, produced by the chained calculation
-- over benchmark_composicion. The table name predates the split,
-- like the composition table's; see 53.
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS benchmark (
    tipo         TEXT NOT NULL DEFAULT 'target'
        CONSTRAINT ck_benchmark_tipo
        CHECK (tipo IN ('target', 'benchmark')),
    fondo        INTEGER NOT NULL,
    nombre       TEXT NOT NULL,
    descripcion  TEXT,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (tipo, fondo),
    CONSTRAINT ck_benchmark_nombre CHECK (length(trim(nombre)) > 0)
);
