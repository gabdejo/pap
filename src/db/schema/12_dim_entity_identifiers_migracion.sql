-- 12_dim_entity_identifiers_migracion.sql
-- ---------------------------------------------------------------
-- Convergence for dim_entity_identifiers (13), which changed shape in
-- release 0.1.8: two observation-date columns, a four-column primary
-- key, a partial unique index on the primary identifier, and a view
-- over the new columns.
--
-- 13 is CREATE TABLE IF NOT EXISTS, which does nothing at all on a
-- database that already has the table - and then its CREATE VIEW
-- reads first_seen_date and fails. create_schema stops at the first
-- file that fails, so on every database created before 0.1.8 NOTHING
-- after 13 was applied: not the scheduler tables (36-38), not the SPP
-- block (50-59). The API still started, with one WARNING nobody reads.
--
-- This file sorts before 13 on purpose. On a fresh database the table
-- does not exist yet, every block is skipped, and 13 creates it in its
-- final shape. On an older database the blocks bring the table up to
-- that shape first, so 13 then runs clean. Idempotent both ways.
--
-- The primary key swap is only safe when the old key's rows are unique
-- under the new one as well, which is always true (the new key is a
-- superset). It is done in place because there is no data migration to
-- run: the new columns start NULL, which 13 documents as "unknown".
-- ---------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'dim_entity_identifiers'
    ) THEN
        RETURN;   -- fresh database: 13 will create the table as it should be
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'dim_entity_identifiers' AND column_name = 'first_seen_date'
    ) THEN
        ALTER TABLE dim_entity_identifiers ADD COLUMN first_seen_date DATE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'dim_entity_identifiers' AND column_name = 'last_seen_date'
    ) THEN
        ALTER TABLE dim_entity_identifiers ADD COLUMN last_seen_date DATE;
    END IF;

    -- Old key: (entity_id, id_type, source). New key adds id_value.
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'dim_entity_identifiers_pkey'
          AND conrelid = 'dim_entity_identifiers'::regclass
          AND array_length(conkey, 1) = 3
    ) THEN
        ALTER TABLE dim_entity_identifiers DROP CONSTRAINT dim_entity_identifiers_pkey;
        ALTER TABLE dim_entity_identifiers
            ADD CONSTRAINT dim_entity_identifiers_pkey
            PRIMARY KEY (entity_id, id_type, source, id_value);
    END IF;
END $$;
