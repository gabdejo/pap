-- 47_stg_prices_bloomberg_dividends.sql
-- ---------------------------------------------------------------
-- Staging for the Bloomberg bulk field EQY_DVD_ADJUST_FACT: the price
-- adjustment factors Bloomberg itself applies, exactly as returned.
-- Loaded by pipelines/prices/bloomberg/dividends/extract.py.
--
-- One row per DISTINCT row Bloomberg has ever returned. The bulk hands
-- back a security's whole history on every call, so the usual
-- "append every load" staging would store the same rows daily. Here the
-- UNIQUE key is the full row: an unchanged row is skipped, and a revised
-- one (same event, different factor) lands as a NEW row next to the old
-- one. loaded_at is therefore "first seen", and the table is the audit
-- trail of revisions - NOT the current set. The current set is
-- fact_adjustment_factors.
--
--   action_filter   the CORPORATE_ACTIONS_FILTER override the row came
--                   back under: CAPITAL_CHANGE, ABNORMAL_CASH, NORMAL_CASH.
--                   The bulk only returns one type per call.
--   operator_type   1 = divide, 2 = multiply, 3 = add (prior prices)
--   flag            1 = prices only, 3 = prices and volume
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_prices_bloomberg_dividends (
    parsekyable    TEXT NOT NULL,
    action_filter  TEXT NOT NULL,
    date           DATE NOT NULL,
    factor         DOUBLE PRECISION NOT NULL,
    operator_type  SMALLINT NOT NULL,
    flag           SMALLINT NOT NULL,
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (parsekyable, action_filter, date, factor, operator_type, flag)
);

CREATE INDEX IF NOT EXISTS idx_stg_prices_bloomberg_dividends_loaded_at
    ON stg_prices_bloomberg_dividends (loaded_at);
