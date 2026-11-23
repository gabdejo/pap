# tests/analytics/benchmark/test_delete.py
# ---------------------------------------------------------------
# Removing a benchmark (removal.delete_benchmark): the fund link,
# the versions with their constituents, the level series with its
# prices, the portfolio and the index entity all go, and nothing else
# does. Needs a scratch PostgreSQL database (PAP_TEST_DBNAME) and is
# SKIPPED without one - same contract as test_assignment.py.
# ---------------------------------------------------------------

import datetime as dt
import os

import psycopg
import pytest
from psycopg.rows import dict_row

from src.db.bootstrap import create_schema
from src.db.queries import get_or_create_entity_id
from src.pipelines.analytics.benchmark import definitions, removal
from src.pipelines.analytics.benchmark.materialize import SOURCE_MODEL

DBNAME = os.environ.get("PAP_TEST_DBNAME")
pytestmark = pytest.mark.skipif(
    not DBNAME, reason="set PAP_TEST_DBNAME to a scratch database to run the DB tests")


@pytest.fixture
def conn():
    from src.shared.config_pg import get_db_config
    cfg = {**get_db_config(), "dbname": DBNAME}
    connection = psycopg.connect(
        host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"],
        user=cfg["user"], password=cfg["password"], row_factory=dict_row)
    try:
        create_schema(connection)
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _count(conn, sql, *params):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchone()["n"]


def test_delete_removes_the_benchmark_and_everything_on_it(conn):
    gone = definitions.ensure_benchmark(conn, "TEST_GONE", "Goes away")
    stays = definitions.ensure_benchmark(conn, "TEST_STAYS", "Stays")
    fund = get_or_create_entity_id(conn, "SPP_TEST_F2", "fund", name="Test Fondo 2")
    definitions.assign(conn, gone["portfolio_id"], fund)
    definitions.assign(conn, stays["portfolio_id"], fund)
    # A version with one constituent (the other benchmark's own series
    # serves as a constituent), and two levels of the series.
    definitions.add_version(
        conn, gone["portfolio_id"], effective_from=dt.date(2026, 1, 2),
        method="buy_and_hold", change_type="initial", reason="test",
        constituents=[{"label": "x", "series_id": stays["series_id"], "weight": 100}])
    cur = conn.cursor()
    for day, level in ((dt.date(2026, 1, 2), 100.0), (dt.date(2026, 1, 5), 101.0)):
        cur.execute("INSERT INTO fact_prices (series_id, date, price, source) VALUES (%s, %s, %s, %s)",
                    (gone["series_id"], day, level, SOURCE_MODEL))

    result = removal.delete_benchmark(conn, gone["portfolio_id"])

    assert result["procode"] == "TEST_GONE"
    assert result["removed"]["dim_portfolio"] == 1
    assert result["removed"]["dim_benchmark_version"] == 1
    assert result["removed"]["benchmark_constituent"] == 1
    assert result["removed"]["fact_prices"] == 2
    assert result["removed"]["benchmark_assignment"] == 1
    assert definitions.benchmark_by_procode(conn, "TEST_GONE") is None
    assert _count(conn, "SELECT COUNT(*) AS n FROM series_registry WHERE series_id = %s",
                  gone["series_id"]) == 0
    assert _count(conn, "SELECT COUNT(*) AS n FROM dim_entity WHERE entity_id = %s",
                  gone["entity_id"]) == 0
    # The other benchmark, its link and the fund entity are untouched.
    other = definitions.benchmark_by_procode(conn, "TEST_STAYS")
    assert other is not None and other["assigned_entity_id"] == fund
    assert _count(conn, "SELECT COUNT(*) AS n FROM dim_entity WHERE entity_id = %s", fund) == 1


def test_delete_refuses_an_unknown_benchmark(conn):
    with pytest.raises(LookupError):
        removal.delete_benchmark(conn, 999_999)
