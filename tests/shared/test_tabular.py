# tests/shared/test_tabular.py
# ---------------------------------------------------------------
# Pins down the tolerant number/date parsing in src/shared/tabular.py.
#
#   - num_flexible strips BOTH non-breaking spaces Excel/es-locale
#     emit as thousands separators (U+202F and U+00A0) - missing the
#     NBSP made every value >= 1000 vanish as an empty cell
#   - comma-decimal vs point-decimal conversion
#   - fecha_flexible reads dd/mm text day-first and Excel serials
# ---------------------------------------------------------------

import datetime as dt

from src.shared.tabular import estilo_decimal, fecha_flexible, num_flexible


def test_num_nbsp_thousands_comma_decimal():
    assert num_flexible("1 234,56", True) == 1234.56


def test_num_narrow_nbsp_thousands():
    assert num_flexible("1 234,56", True) == 1234.56


def test_num_comma_vs_point_decimal():
    assert num_flexible("128,4471", True) == 128.4471
    assert num_flexible("1.234,56", True) == 1234.56
    assert num_flexible("1,234.56", False) == 1234.56


def test_num_null_tokens_and_typed():
    assert num_flexible("n.d.", True) is None
    assert num_flexible("#N/A", False) is None
    assert num_flexible(True, False) is None
    assert num_flexible(128.5, True) == 128.5
    assert num_flexible(float("nan"), True) is None


def test_fecha_dayfirst_text_and_serial():
    assert fecha_flexible("05/08/1993") == dt.date(1993, 8, 5)
    assert fecha_flexible("1993-08-05") == dt.date(1993, 8, 5)
    assert fecha_flexible(34551) == dt.date(1994, 8, 5)   # serial de Excel
    assert fecha_flexible("no es fecha") is None


def test_estilo_decimal():
    assert estilo_decimal(["128,4471", "142,88"]) is True
    assert estilo_decimal(["128.4471", "142.88"]) is False
    assert estilo_decimal([]) is None
