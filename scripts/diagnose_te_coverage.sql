-- diagnose_te_coverage.sql
-- ---------------------------------------------------------------
-- Price coverage of one fund's securities on one date, as the
-- tracking error module will see it (docs/TRACKING_ERROR_INPUTS.md
-- A2). One row per held security and candidate price series:
-- weight, series, first/last price date and observations in the
-- trailing 3 years.
--
-- The module uses ONE series per security: PX_CLEAN_PCT/sbs for
-- bonds (ind_clase 145) when it exists, else PX_LAST/bloomberg when
-- active, else PX_LAST/sbs (tracking_error/data.py::select_series).
-- Under 252 observations the beta-scaled backfill fills the rest;
-- under 60 the name runs on its bucket alone. A security with no
-- row here but a weight has no price series at all.
--
-- DBeaver binds :procode and :as_of when it runs.
-- ---------------------------------------------------------------

WITH held AS (
    SELECT u.security_entity_id AS entity_id, u.codigo_iso_moneda, u.weight, s.ind_clase
    FROM vw_positions_unified u
    JOIN dim_portfolio p ON p.portfolio_id = u.portfolio_id AND p.source = u.source
    JOIN fact_positions_securities s
      ON  s.portfolio_id = u.portfolio_id AND s.date = u.date AND s.source = u.source
      AND s.security_entity_id = u.security_entity_id
    WHERE p.procode = :procode
      AND u.date = :as_of
      AND u.position_type = 'security'
)
SELECT
    e.procode,
    e.name,
    h.ind_clase,
    h.codigo_iso_moneda,
    ROUND(h.weight::numeric, 6)   AS weight,
    sr.source,
    sr.field,
    sr.status,
    MIN(fp.date)                  AS first_price,
    MAX(fp.date)                  AS last_price,
    COUNT(fp.date) FILTER (WHERE fp.date > CAST(:as_of AS date) - 1095) AS obs_3y
FROM held h
JOIN dim_entity e ON e.entity_id = h.entity_id
LEFT JOIN series_registry sr
       ON  sr.entity_id = h.entity_id
       AND (   (sr.field = 'PX_LAST' AND sr.source IN ('bloomberg', 'sbs'))
            OR (sr.field = 'PX_CLEAN_PCT' AND sr.source = 'sbs'))
LEFT JOIN fact_prices fp
       ON fp.series_id = sr.series_id AND fp.date <= CAST(:as_of AS date)
GROUP BY e.procode, e.name, h.ind_clase, h.codigo_iso_moneda, h.weight,
         sr.source, sr.field, sr.status
ORDER BY h.weight DESC NULLS LAST, e.procode, sr.source, sr.field;
