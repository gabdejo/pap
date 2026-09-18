-- 59_tradebook_migraciones.sql
-- ---------------------------------------------------------------
-- Convergence file for the tradebook (58).
--
-- Same reason as 56_spp_migraciones.sql: 58 is CREATE TABLE IF NOT
-- EXISTS, which does nothing at all on a database that already has
-- the table. A column or a CHECK that changes shape after the first
-- deployment never reaches that database on its own, and the
-- mismatch shows up as a runtime error instead of a missing feature.
--
-- This file runs after 58 and re-states, idempotently, what the CODE
-- depends on. Every block is safe on a fresh database and on one
-- created by any earlier version of this branch.
-- ---------------------------------------------------------------

-- ---- The two sources, and who traded
--
-- The first cut of this table had origen IN ('excel','manual','posiciones'):
-- a third value for rows DERIVED from day-to-day changes in holdings. That
-- idea is gone - a change in quantity is not a trade, and the derivation
-- proposed more noise than trades. In its place the book is fed by FMS,
-- which is an observed operation like the others, only without a trader's
-- name on it.
--
-- `trader` comes with it, and the CHECK that pairs them: mandatory where the
-- row is somebody's registration, absent where it is the system's. That rule
-- is what keeps the by-trader view honest - without it a careless load would
-- leave ownerless rows and the split would understate somebody's book while
-- nothing failed.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tradebook' AND column_name = 'trader'
    ) THEN
        ALTER TABLE tradebook ADD COLUMN trader TEXT;
    END IF;
END $$;

-- Rows that predate the split: anything derived from holdings is dropped
-- rather than relabelled. It was never an observed trade, and keeping it
-- under a new name would launder an inference into a fact.
DELETE FROM tradebook WHERE origen = 'posiciones';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_tradebook_origen'
          AND conrelid = 'tradebook'::regclass
          AND pg_get_constraintdef(oid) LIKE '%posiciones%'
    ) THEN
        ALTER TABLE tradebook DROP CONSTRAINT ck_tradebook_origen;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_tradebook_origen'
          AND conrelid = 'tradebook'::regclass
    ) THEN
        ALTER TABLE tradebook
            ADD CONSTRAINT ck_tradebook_origen
            CHECK (origen IN ('fms', 'excel', 'manual'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_tradebook_trader'
          AND conrelid = 'tradebook'::regclass
    ) THEN
        -- NOT VALID: the constraint governs every row written from here on,
        -- but adding it does not fail on a database that already holds rows
        -- without a trader. Those are visible in the tablero, which is where
        -- they get fixed; validating by hand afterwards is one command.
        ALTER TABLE tradebook
            ADD CONSTRAINT ck_tradebook_trader
            CHECK ((origen = 'fms' AND trader IS NULL)
                   OR (origen <> 'fms' AND length(trim(trader)) > 0))
            NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_tradebook_trader ON tradebook (trader);
CREATE INDEX IF NOT EXISTS ix_tradebook_origen ON tradebook (origen);

-- ---------------------------------------------------------------
-- La fecha de liquidacion sale del libro (2026-09-17). Era el campo
-- que nadie llenaba: salio del formulario, y con el del formato de
-- Excel, del lector y del INSERT. Una columna que nada escribe es una
-- promesa vacia en el esquema; se quita. DROP ... IF EXISTS: en una
-- base creada despues de este cambio no hay nada que quitar.
-- ---------------------------------------------------------------
ALTER TABLE tradebook DROP CONSTRAINT IF EXISTS ck_tradebook_liquidacion;
ALTER TABLE tradebook DROP COLUMN IF EXISTS fecha_liquidacion;

-- ---------------------------------------------------------------
-- Operador (2026-09-18): quien ejecuto, distinto del book. Opcional,
-- asi que la columna entra sin tocar las filas que ya estan.
-- ---------------------------------------------------------------
ALTER TABLE tradebook ADD COLUMN IF NOT EXISTS operador TEXT;
