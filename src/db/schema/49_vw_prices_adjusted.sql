-- 49_vw_prices_adjusted.sql
-- ---------------------------------------------------------------
-- vw_prices_adjusted: fact_prices (raw) back-adjusted for dividends and
-- capital changes with the Bloomberg factors in fact_adjustment_factors.
--
-- Grain: (series_id, date), every PX_LAST series of every source. A
-- series whose entity has no factors (indices, SPP NAVs, bonds, names
-- Bloomberg does not cover) comes through with adj_factor = 1.
--
-- Columns:
--   price            raw, as stored
--   adj_factor       product of every factor dated STRICTLY AFTER `date`
--   price_adjusted   price * adj_factor - reproduces Bloomberg's fully
--                    adjusted PX_LAST (scripts/diagnose_bloomberg_dividends.py)
--   total_return     price_adjusted / previous price_adjusted - 1, over
--                    the series' own consecutive dates
--
-- price_adjusted is AS OF TODAY: each new dividend rescales every
-- earlier row of that entity. Never store it. total_return for a given
-- date does not change once that date's factor has arrived, so anything
-- stored from this view (e.g. a future 'market' contribution method)
-- must be built on total_return or on RATIOS of price_adjusted read in
-- one query, never on its level. The benchmark materializer does not
-- read this view: it applies the factors to its units itself, so its
-- stored holdings keep market prices.
--
-- Nothing is materialised: a late or corrected factor shows up on the
-- next read. Filter by series_id - it is pushed down into the view. A
-- date filter is applied after the full series is computed, which is
-- required (the previous row and all later factors are needed).
--
-- Factors apply to every price source of the entity, including the SBS
-- vector. If the SBS price reflects an ex-date a day apart from
-- Bloomberg's, total_return shows a false drop and rebound there.
-- ---------------------------------------------------------------

CREATE OR REPLACE VIEW vw_prices_adjusted AS
WITH adj AS (
    SELECT
        p.series_id,
        sr.entity_id,
        sr.source,
        p.date,
        p.price,
        COALESCE(EXP(SUM(LN(f.factor))), 1) AS adj_factor
    FROM fact_prices p
    JOIN series_registry sr ON sr.series_id = p.series_id
    LEFT JOIN fact_adjustment_factors f
        ON  f.entity_id = sr.entity_id
        AND f.source    = 'bloomberg'
        AND f.date      > p.date
    WHERE sr.field = 'PX_LAST'
    GROUP BY p.series_id, sr.entity_id, sr.source, p.date, p.price
)
SELECT
    series_id,
    entity_id,
    source,
    date,
    price,
    adj_factor,
    price * adj_factor AS price_adjusted,
    price * adj_factor
        / NULLIF(LAG(price * adj_factor) OVER (PARTITION BY series_id ORDER BY date), 0)
        - 1 AS total_return
FROM adj;
