# tests/prices/bloomberg/test_dividends_db.py
# ---------------------------------------------------------------
# The SQL half of the adjustment-factor pipeline against a real
# Postgres: load_factors reconciling (insert / update / delete, and
# leaving absent pairs alone) and vw_prices_adjusted's arithmetic.
#
# Needs a scratch database, same convention as
# tests/analytics/benchmark/test_changelog.py:
#
#   set PAP_TEST_DBNAME=pap_test
#   python -m pytest tests/prices/bloomberg/test_dividends_db.py -q
#
# Everything runs in one transaction that is rolled back.
# ---------------------------------------------------------------

import datetime as dt
import os

import pandas as pd
import psycopg
import pytest
from psycopg.rows import dict_row

from src.db.bootstrap import create_schema
from src.pipelines.prices.bloomberg.dividends.loader import load_factors
from src.pipelines.prices.bloomberg.dividends.transform import COLUMNS

DBNAME = os.environ.get("PAP_TEST_DBNAME")
pytestmark = pytest.mark.skipif(
    not DBNAME, reason="set PAP_TEST_DBNAME to a scratch database to run the DB tests")

D1, D2, D3, D4 = (dt.date(2026, 1, d) for d in (5, 6, 7, 8))


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


def _priced_series(conn, procode, prices):
    from src.db.queries import get_or_create_entity_id
    entity_id = get_or_create_entity_id(conn, procode, "equity", name=procode)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO series_registry (entity_id, field, domain, source, frequency, status)
        VALUES (%s, 'PX_LAST', 'prices', 'test', 'daily', 'active')
        RETURNING series_id
        """, (entity_id,))
    series_id = cur.fetchone()["series_id"]
    for day, px in prices.items():
        cur.execute(
            "INSERT INTO fact_prices (series_id, date, price, source) VALUES (%s, %s, %s, 'test')",
            (series_id, day, px))
    return entity_id, series_id


def _factors(rows):
    return pd.DataFrame([[*r, "bloomberg"] for r in rows], columns=COLUMNS)


def _stored(conn, entity_id):
    cur = conn.cursor()
    cur.execute(
        "SELECT date, action_type, factor FROM fact_adjustment_factors "
        "WHERE entity_id = %s ORDER BY date, action_type", (entity_id,))
    return [(r["date"], r["action_type"], r["factor"]) for r in cur.fetchall()]


def test_view_back_adjusts_and_returns_ignore_the_ex_date_drop(conn):
    # 2:1 split on D3 (price halves), then a 2% dividend on D4 (price drops 2%).
    entity_id, series_id = _priced_series(conn, "DIVTEST_A", {D1: 100.0, D2: 100.0, D3: 50.0, D4: 49.0})
    load_factors(conn, _factors([
        (entity_id, D3, "CAPITAL_CHANGE", 0.5),
        (entity_id, D4, "NORMAL_CASH", 0.98),
    ]))

    cur = conn.cursor()
    cur.execute(
        "SELECT date, adj_factor, price_adjusted, total_return FROM vw_prices_adjusted "
        "WHERE series_id = %s ORDER BY date", (series_id,))
    rows = cur.fetchall()

    assert [r["adj_factor"] for r in rows] == pytest.approx([0.49, 0.49, 0.98, 1.0])
    assert [r["price_adjusted"] for r in rows] == pytest.approx([49.0, 49.0, 49.0, 49.0])
    assert rows[0]["total_return"] is None
    assert [r["total_return"] for r in rows[1:]] == pytest.approx([0.0, 0.0, 0.0], abs=1e-12)


def test_series_without_factors_passes_through(conn):
    _, series_id = _priced_series(conn, "DIVTEST_B", {D1: 10.0, D2: 11.0})
    cur = conn.cursor()
    cur.execute(
        "SELECT adj_factor, price_adjusted, total_return FROM vw_prices_adjusted "
        "WHERE series_id = %s ORDER BY date", (series_id,))
    rows = cur.fetchall()
    assert [r["adj_factor"] for r in rows] == [1.0, 1.0]
    assert rows[1]["total_return"] == pytest.approx(0.1)


def test_load_reconciles_only_the_pairs_it_was_given(conn):
    entity_id, _ = _priced_series(conn, "DIVTEST_C", {D1: 1.0})
    first = load_factors(conn, _factors([
        (entity_id, D1, "NORMAL_CASH", 0.99),
        (entity_id, D2, "NORMAL_CASH", 0.98),
        (entity_id, D2, "CAPITAL_CHANGE", 0.5),
    ]))
    assert (first["inserted"], first["updated"], first["deleted"], first["earliest"]) == (3, 0, 0, D1)

    same = load_factors(conn, _factors([
        (entity_id, D1, "NORMAL_CASH", 0.99),
        (entity_id, D2, "NORMAL_CASH", 0.98),
    ]))
    assert (same["inserted"], same["updated"], same["deleted"], same["earliest"]) == (0, 0, 0, None)

    # NORMAL_CASH: D1 withdrawn, D2 revised, D3 new. CAPITAL_CHANGE absent -> untouched.
    second = load_factors(conn, _factors([
        (entity_id, D2, "NORMAL_CASH", 0.97),
        (entity_id, D3, "NORMAL_CASH", 0.96),
    ]))
    assert (second["inserted"], second["updated"], second["deleted"], second["earliest"]) == (1, 1, 1, D1)
    assert _stored(conn, entity_id) == [
        (D2, "CAPITAL_CHANGE", 0.5), (D2, "NORMAL_CASH", 0.97), (D3, "NORMAL_CASH", 0.96),
    ]
