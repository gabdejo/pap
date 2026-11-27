-- 41_dim_benchmark_version.sql
-- ---------------------------------------------------------------
-- dim_benchmark_version: the append-only definition history of a
-- hand-made benchmark (composite index), one row per version.
--
-- A benchmark is PORTFOLIO-SHAPED (docs/PENDING_PIPELINES.md item 5):
--   dim_portfolio  row with portfolio_type 'benchmark', source 'model',
--                  bridged by parent_entity_id to ...
--   dim_entity     a row of entity_type 'index' that carries the level
--                  series (series_registry PX_LAST, source 'model',
--                  levels in fact_prices).
-- This table holds WHAT the benchmark is made of over time; the
-- analytics module (src/pipelines/analytics/benchmark/) turns each
-- version into facts.
--
-- APPEND-ONLY. A change never edits a row: it inserts a new version
-- with its own effective_from, a change_type and a mandatory reason,
-- and names the version it supersedes. Nothing in the code updates or
-- deletes here; the changelog (44_vw_benchmark_changelog.sql) is a
-- view over this history, not a process.
--
-- Two clocks, on purpose: effective_from is when a composition
-- GOVERNS; created_at is when it was RECORDED. They diverge whenever a
-- methodology change restates the past, and both have to be kept.
--
-- Several versions may share an effective_from (a correction restates
-- the same start date): the highest version_id governs that date range.
--
--   method      buy_and_hold   weights set at effective_from, holdings
--                              drift with prices until the next version
--               fixed_weights  weights restored every rebalance_frequency
--                              (monthly | quarterly | annual; 'daily'
--                              is added by 50_benchmark_rebalance_daily.sql)
--               heuristic      a named rule; reserved, not materialized
--   change_type initial | rebalance | methodology | constituent_change
--               | correction
-- ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_benchmark_version (
    version_id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    portfolio_id          INTEGER NOT NULL REFERENCES dim_portfolio (portfolio_id),

    effective_from        DATE NOT NULL,
    method                TEXT NOT NULL
        CHECK (method IN ('buy_and_hold', 'fixed_weights', 'heuristic')),
    rebalance_frequency   TEXT
        CHECK (rebalance_frequency IN ('monthly', 'quarterly', 'annual')
               OR rebalance_frequency IS NULL),
    change_type           TEXT NOT NULL
        CHECK (change_type IN ('initial', 'rebalance', 'methodology',
                               'constituent_change', 'correction')),
    reason                TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    support_doc           TEXT,                       -- path on the network share
    recorded_by           TEXT,
    supersedes_version_id INTEGER REFERENCES dim_benchmark_version (version_id),

    created_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- A rebalance frequency means fixed weights, and fixed weights need one.
    CHECK ((method = 'fixed_weights') = (rebalance_frequency IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_dim_benchmark_version_portfolio_effective
    ON dim_benchmark_version (portfolio_id, effective_from, version_id);
