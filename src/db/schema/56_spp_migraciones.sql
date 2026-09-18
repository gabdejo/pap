-- 56_spp_migraciones.sql
-- ---------------------------------------------------------------
-- Convergence file for the SPP block (50-55).
--
-- Every other schema file is CREATE TABLE IF NOT EXISTS, which is a
-- no-op on a database whose tables already exist: a constraint that
-- changes shape after the first deployment NEVER reaches an older
-- database, and the mismatch only surfaces as a runtime error.
-- (It already happened once: benchmark_composicion's fuente CHECK
-- gained 'manual' in the 2026-09 redesign and had to be ALTERed by
-- hand here.)
--
-- This file runs last (alphabetically after 55_) and re-states the
-- constraints the CODE depends on, idempotently: every block is safe
-- to run on a fresh database and on one created by any earlier
-- version of this branch. Add to it whenever a 50-55 constraint
-- changes - never edit the constraint in place and hope.
-- ---------------------------------------------------------------

-- ---- dim_entity: ON CONFLICT target used by get_or_create_entity_id
-- src/db/queries.py upserts on (procode, entity_type). Databases
-- created before that constraint existed raise InvalidColumnReference
-- on the FIRST registration of the SPP series.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_dim_entity_procode_type'
          AND conrelid = 'dim_entity'::regclass
    ) THEN
        ALTER TABLE dim_entity
            ADD CONSTRAINT uq_dim_entity_procode_type
            UNIQUE (procode, entity_type);
    END IF;
END $$;


-- ---- benchmark_composicion: the 'manual' source
-- Components price from three stores since the 2026-09 redesign
-- (bloomberg / fact / manual). A database created before it keeps the
-- two-value CHECK and rejects every manual component with a
-- check_violation the UI can only show as a 500.
ALTER TABLE benchmark_composicion
    DROP CONSTRAINT IF EXISTS benchmark_composicion_fuente_check;
ALTER TABLE benchmark_composicion
    DROP CONSTRAINT IF EXISTS ck_benchmark_composicion_fuente;
ALTER TABLE benchmark_composicion
    ADD CONSTRAINT ck_benchmark_composicion_fuente
    CHECK (fuente IN ('bloomberg', 'fact', 'manual'));

ALTER TABLE benchmark_composicion
    DROP CONSTRAINT IF EXISTS benchmark_composicion_fx_fuente_check;
ALTER TABLE benchmark_composicion
    DROP CONSTRAINT IF EXISTS ck_benchmark_composicion_fx_fuente;
ALTER TABLE benchmark_composicion
    ADD CONSTRAINT ck_benchmark_composicion_fx_fuente
    CHECK (fx_fuente IN ('bloomberg', 'fact', 'manual'));


-- ---- Indices compuestos: target y benchmark (2026-09-16)
-- The one composite index became two, told apart by a `tipo` column
-- on both tables, with the key widened to include it. Everything a
-- database held before the split IS the target - that is what the
-- desk had been building under the name "benchmark" - so the new
-- column defaults to 'target' and the existing level series is
-- renamed from SPP_BENCH_F{n} to SPP_TARGET_F{n}. SPP_BENCH_F{n} is
-- then free for the new benchmark, which the registration creates
-- empty on the next start.
--
-- The rename is a ONE-SHOT and lives inside the block that adds the
-- column: a database is pre-split exactly when the composition table
-- lacks `tipo`, and only then may an SPP_BENCH entity be the target
-- in disguise. Guarding on "no SPP_TARGET exists yet" instead would
-- rename a genuine benchmark the day a fund gets one before a target.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'benchmark_composicion')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'benchmark_composicion' AND column_name = 'tipo')
    THEN
        ALTER TABLE benchmark_composicion
            ADD COLUMN tipo TEXT NOT NULL DEFAULT 'target';
        UPDATE dim_entity
           SET procode = 'SPP_TARGET_' || substr(procode, 11),
               name    = replace(name, 'Benchmark', 'Target')
         WHERE procode LIKE 'SPP\_BENCH\_F%' AND entity_type = 'index';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'benchmark_composicion') THEN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conname = 'ck_benchmark_composicion_tipo'
                         AND conrelid = 'benchmark_composicion'::regclass) THEN
            ALTER TABLE benchmark_composicion
                ADD CONSTRAINT ck_benchmark_composicion_tipo
                CHECK (tipo IN ('target', 'benchmark'));
        END IF;
        -- Old key had four columns; the new one leads with tipo.
        IF EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'benchmark_composicion_pkey'
                     AND conrelid = 'benchmark_composicion'::regclass
                     AND array_length(conkey, 1) = 4) THEN
            ALTER TABLE benchmark_composicion DROP CONSTRAINT benchmark_composicion_pkey;
            ALTER TABLE benchmark_composicion
                ADD CONSTRAINT benchmark_composicion_pkey
                PRIMARY KEY (tipo, fondo, vigente_desde, fuente, ref_id);
        END IF;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'benchmark') THEN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'benchmark' AND column_name = 'tipo') THEN
            ALTER TABLE benchmark ADD COLUMN tipo TEXT NOT NULL DEFAULT 'target';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conname = 'ck_benchmark_tipo'
                         AND conrelid = 'benchmark'::regclass) THEN
            ALTER TABLE benchmark
                ADD CONSTRAINT ck_benchmark_tipo
                CHECK (tipo IN ('target', 'benchmark'));
        END IF;
        IF EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'benchmark_pkey'
                     AND conrelid = 'benchmark'::regclass
                     AND array_length(conkey, 1) = 1) THEN
            ALTER TABLE benchmark DROP CONSTRAINT benchmark_pkey;
            ALTER TABLE benchmark ADD CONSTRAINT benchmark_pkey PRIMARY KEY (tipo, fondo);
        END IF;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_benchmark_composicion_tipo_fondo
    ON benchmark_composicion (tipo, fondo, vigente_desde);
