-- 48_fact_adjustment_factors.sql
-- ---------------------------------------------------------------
-- Price adjustment factors per entity: the CURRENT set, one row per
-- (entity, ex-date, action type, source).
--
-- factor is normalised to ONE convention whatever the vendor's operator:
-- MULTIPLY every price strictly before `date` by it. A 2:1 split is 0.5,
-- a 2% cash dividend is ~0.98. vw_prices_adjusted (schema 49) is the
-- only reader and derives adjusted prices and total returns from it;
-- fact_prices stays raw and append-only.
--
-- Deviations from the fact-table norm, both deliberate:
--   - Keyed on entity_id, not series_id: a factor belongs to the
--     instrument and applies to every price series of it (Bloomberg's
--     and the SBS's), and it needs no series_registry row - the universe
--     is whatever holds an active PX_LAST/bloomberg series.
--   - Rows are UPDATED and DELETED, not only inserted: the vendor
--     re-sends full history, corrects past factors and occasionally
--     withdraws one. loaded_at moves when a factor changes.
--
-- action_type: CAPITAL_CHANGE (splits, stock dividends, spin-offs),
-- ABNORMAL_CASH, NORMAL_CASH. Kept so a split-only (price return)
-- series can be derived later without re-pulling.
--
-- The SBS dividend factors are NOT here: they stay in fact_prices as
-- DIV_ADJ_FACTOR/sbs, unused by the view.
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fact_adjustment_factors (
    entity_id    INTEGER NOT NULL REFERENCES dim_entity (entity_id),
    date         DATE NOT NULL,
    action_type  TEXT NOT NULL,
    factor       DOUBLE PRECISION NOT NULL CHECK (factor > 0),
    source       TEXT NOT NULL,
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (entity_id, date, action_type, source)
);

CREATE INDEX IF NOT EXISTS idx_fact_adjustment_factors_date
    ON fact_adjustment_factors (date);
