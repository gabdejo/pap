-- 50_benchmark_rebalance_daily.sql
-- ---------------------------------------------------------------
-- Adds 'daily' to dim_benchmark_version.rebalance_frequency.
--
-- The CHECK was declared inline in 41_dim_benchmark_version.sql, so a
-- CREATE TABLE IF NOT EXISTS cannot widen it on a database that already
-- has the table. This replaces it by the name Postgres gave it
-- (<table>_<column>_check). Re-runnable: drop if there, add again.
--
-- 'daily' re-strikes a fixed_weights basket on every date a constituent
-- quotes (src/pipelines/analytics/benchmark/materialize.py), so the
-- stored weights are the declared ones on every date.
-- ---------------------------------------------------------------

ALTER TABLE dim_benchmark_version
    DROP CONSTRAINT IF EXISTS dim_benchmark_version_rebalance_frequency_check;

ALTER TABLE dim_benchmark_version
    ADD CONSTRAINT dim_benchmark_version_rebalance_frequency_check
    CHECK (rebalance_frequency IN ('daily', 'monthly', 'quarterly', 'annual')
           OR rebalance_frequency IS NULL);
