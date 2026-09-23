-- scripts/reparar_restricciones.sql
-- ---------------------------------------------------------------
-- Repara las restricciones UNIQUE que el registro de series SPP
-- necesita y que faltan en bases creadas por versiones anteriores.
--
-- El sintoma es este error al registrar series o cargar el historico:
--   there is no unique or exclusion constraint matching the
--   ON CONFLICT specification
--
-- Pasa porque el esquema se escribe con CREATE TABLE IF NOT EXISTS,
-- que NO altera una tabla que ya existe: una restriccion agregada
-- despues nunca llega a una base creada antes.
--
-- Como correrlo (cualquiera de las dos):
--   - pgAdmin: abre la base del tablero y pega todo esto en Query Tool
--   - consola:  psql -d pap -f scripts\reparar_restricciones.sql
--
-- Es seguro repetirlo: si la restriccion ya existe, no hace nada.
-- Desde la version del 2026-09-14 esto se aplica solo al abrir el
-- tablero (56_spp_migraciones.sql), y este archivo deja de hacer
-- falta.
-- ---------------------------------------------------------------

-- ---- 1. Filas que impedirian crear las restricciones --------------
-- Si alguna de estas consultas devuelve filas, hay duplicados que
-- resolver A MANO antes de seguir (decide cual conservar y borra el
-- resto). Si las tres salen vacias, continua sin mas.

SELECT 'dim_entity duplicado' AS problema, procode, entity_type, count(*) AS veces
FROM dim_entity
GROUP BY procode, entity_type
HAVING count(*) > 1;

SELECT 'series_registry duplicado' AS problema, entity_id, field, source, count(*) AS veces
FROM series_registry
GROUP BY entity_id, field, source
HAVING count(*) > 1;

SELECT 'dim_security duplicado' AS problema, entity_id, count(*) AS veces
FROM dim_security
GROUP BY entity_id
HAVING count(*) > 1;


-- ---- 2. Crear lo que falte ----------------------------------------

DO $$
DECLARE
    creadas INTEGER := 0;
BEGIN
    -- dim_entity (procode, entity_type): la que usa
    -- get_or_create_entity_id, y la que falla primero.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'dim_entity'::regclass AND contype = 'u'
          AND pg_get_constraintdef(oid) = 'UNIQUE (procode, entity_type)'
    ) THEN
        ALTER TABLE dim_entity
            ADD CONSTRAINT uq_dim_entity_procode_type UNIQUE (procode, entity_type);
        creadas := creadas + 1;
        RAISE NOTICE 'dim_entity: restriccion creada.';
    ELSE
        RAISE NOTICE 'dim_entity: ya la tenia.';
    END IF;

    -- series_registry (entity_id, field, source): la usa register_series.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'series_registry'::regclass AND contype = 'u'
          AND pg_get_constraintdef(oid) = 'UNIQUE (entity_id, field, source)'
    ) THEN
        ALTER TABLE series_registry
            ADD CONSTRAINT uq_series_registry_entity_field_source
            UNIQUE (entity_id, field, source);
        creadas := creadas + 1;
        RAISE NOTICE 'series_registry: restriccion creada.';
    ELSE
        RAISE NOTICE 'series_registry: ya la tenia.';
    END IF;

    -- dim_security (entity_id): la usa el alta de las entidades SPP.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'dim_security'::regclass AND contype IN ('u', 'p')
          AND pg_get_constraintdef(oid) IN ('UNIQUE (entity_id)', 'PRIMARY KEY (entity_id)')
    ) THEN
        ALTER TABLE dim_security
            ADD CONSTRAINT uq_dim_security_entity UNIQUE (entity_id);
        creadas := creadas + 1;
        RAISE NOTICE 'dim_security: restriccion creada.';
    ELSE
        RAISE NOTICE 'dim_security: ya la tenia.';
    END IF;

    RAISE NOTICE '---';
    IF creadas = 0 THEN
        RAISE NOTICE 'No faltaba ninguna. Si el error persiste, es en otra tabla.';
    ELSE
        RAISE NOTICE '% restriccion(es) creadas. Vuelve a abrir el tablero.', creadas;
    END IF;
END $$;


-- ---- 3. Como quedo ------------------------------------------------

SELECT c.conrelid::regclass::text AS tabla,
       c.conname                  AS restriccion,
       pg_get_constraintdef(c.oid) AS definicion
FROM pg_constraint c
WHERE c.conrelid IN ('dim_entity'::regclass,
                     'series_registry'::regclass,
                     'dim_security'::regclass)
  AND c.contype IN ('u', 'p')
ORDER BY tabla, restriccion;
