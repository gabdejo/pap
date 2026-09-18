# tests/prices/sbs/valor_cuota/test_transform.py
# ---------------------------------------------------------------
# Golden-file tests for the valor_cuota transform. No DB, no SBS.
#
# What is being pinned down:
#   - one staged (afp, fondo, date) row fans out into one fact row
#     per metric WITH a value (a NULL metric produces no row - the
#     long-format property that replaced the monitor's
#     completar_vacios machinery)
#   - metric -> series_registry field mapping (PX_LAST / CUOTAS /
#     FONDO_SOLES)
#   - rows without a registered series are dropped, never raised
#   - empty in => empty out
# ---------------------------------------------------------------

import datetime as dt

import pandas as pd

from src.pipelines.prices.sbs.valor_cuota.transform import FACT_COLUMNS, transform


def _stg_row(**overrides):
    base = {
        "afp": "profuturo",
        "fondo": 2,
        "valor_cuota": 25.1234567,
        "cuotas": 1_000_000.25,
        "fondo_soles": 25_123_456.78,
        "fuente": "extraccion",
        "date": dt.date(2026, 8, 28),
        "loaded_at": dt.datetime(2026, 8, 28, 18, 0, 0),
    }
    base.update(overrides)
    return base


def _stg_df(rows):
    return pd.DataFrame(rows)


def _series_map(*specs):
    """specs: (procode, field, series_id)"""
    return {(p, f): {"series_id": sid, "entity_id": sid, "field": f,
                     "source": "sbs", "procode": p, "name": p}
            for p, f, sid in specs}


FULL_MAP = _series_map(
    ("SPP_PROFUTURO_F2", "PX_LAST", 1),
    ("SPP_PROFUTURO_F2", "CUOTAS", 2),
    ("SPP_PROFUTURO_F2", "FONDO_SOLES", 3),
    ("SPP_HABITAT_F0", "PX_LAST", 4),
)


def test_empty_in_empty_out():
    out = transform(pd.DataFrame(), FULL_MAP)
    assert out.empty
    assert list(out.columns) == FACT_COLUMNS


def test_one_row_fans_out_into_three_facts():
    out = transform(_stg_df([_stg_row()]), FULL_MAP)
    assert len(out) == 3
    assert set(out["series_id"]) == {1, 2, 3}
    assert (out["source"] == "sbs").all()
    assert (out["date"] == dt.date(2026, 8, 28)).all()


def test_metric_values_land_on_their_series():
    out = transform(_stg_df([_stg_row()]), FULL_MAP).set_index("series_id")
    assert out.at[1, "value"] == 25.1234567     # PX_LAST <- valor_cuota
    assert out.at[2, "value"] == 1_000_000.25   # CUOTAS <- cuotas
    assert out.at[3, "value"] == 25_123_456.78  # FONDO_SOLES <- fondo_soles


def test_null_metric_produces_no_row():
    # The historical XLS only brings valor_cuota: the other two must not
    # generate rows (in long format a missing metric is a missing row,
    # never an empty cell that could clobber data).
    out = transform(_stg_df([_stg_row(cuotas=None, fondo_soles=None)]), FULL_MAP)
    assert len(out) == 1
    assert out.iloc[0]["series_id"] == 1


def test_unregistered_series_is_dropped_not_raised():
    # habitat F0 only has PX_LAST registered in this map: cuotas and
    # fondo_soles find no series and are dropped with a warning.
    out = transform(_stg_df([_stg_row(afp="habitat", fondo=0)]), FULL_MAP)
    assert len(out) == 1
    assert out.iloc[0]["series_id"] == 4


def test_unknown_afp_is_dropped_not_raised():
    rows = [_stg_row(), _stg_row(afp="nueva_afp")]
    out = transform(_stg_df(rows), FULL_MAP)
    assert set(out["series_id"]) == {1, 2, 3}


def test_two_dates_stay_separate():
    rows = [_stg_row(cuotas=None, fondo_soles=None),
            _stg_row(cuotas=None, fondo_soles=None,
                     date=dt.date(2026, 8, 29), valor_cuota=25.2)]
    out = transform(_stg_df(rows), FULL_MAP)
    assert len(out) == 2
    assert set(out["date"]) == {dt.date(2026, 8, 28), dt.date(2026, 8, 29)}
