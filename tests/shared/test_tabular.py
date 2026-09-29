# tests/shared/test_tabular.py
# ---------------------------------------------------------------
# Pins down the tolerant number/date parsing in src/shared/tabular.py.
#
#   - parse_number strips BOTH non-breaking spaces Excel/es-locale
#     emit as thousands separators (U+202F and U+00A0) - missing the
#     NBSP made every value >= 1000 vanish as an empty cell
#   - comma-decimal vs point-decimal conversion
#   - parse_date reads dd/mm text day-first and Excel serials
# ---------------------------------------------------------------

import datetime as dt

from src.shared.tabular import decimal_style, parse_date, parse_number


def test_number_nbsp_thousands_comma_decimal():
    assert parse_number("1 234,56", True) == 1234.56


def test_number_narrow_nbsp_thousands():
    assert parse_number("1 234,56", True) == 1234.56


def test_number_comma_vs_point_decimal():
    assert parse_number("128,4471", True) == 128.4471
    assert parse_number("1.234,56", True) == 1234.56
    assert parse_number("1,234.56", False) == 1234.56


def test_number_null_tokens_and_typed():
    assert parse_number("n.d.", True) is None
    assert parse_number("#N/A", False) is None
    assert parse_number(True, False) is None
    assert parse_number(128.5, True) == 128.5
    assert parse_number(float("nan"), True) is None


def test_date_dayfirst_text_and_serial():
    assert parse_date("05/08/1993") == dt.date(1993, 8, 5)
    assert parse_date("1993-08-05") == dt.date(1993, 8, 5)
    assert parse_date(34551) == dt.date(1994, 8, 5)   # Excel serial
    assert parse_date("not a date") is None


def test_decimal_style():
    assert decimal_style(["128,4471", "142,88"]) is True
    assert decimal_style(["128.4471", "142.88"]) is False
    assert decimal_style([]) is None
