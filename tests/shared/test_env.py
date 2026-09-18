# tests/shared/test_env.py
# ---------------------------------------------------------------
# Pins down env accessors:
#   - optional() treats an EMPTY value as unset (regression: the
#     .env.example template ships every key blank, and letting ''
#     win over the default once turned every data path CWD-relative)
#   - required() never falls back
# ---------------------------------------------------------------

import pytest

from src.shared.env import MissingSecret, optional, required


def test_optional_empty_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PRUEBA_VACIA", "")
    assert optional("PRUEBA_VACIA", "defecto") == "defecto"


def test_optional_unset_falls_back(monkeypatch):
    monkeypatch.delenv("PRUEBA_AUSENTE", raising=False)
    assert optional("PRUEBA_AUSENTE", "defecto") == "defecto"


def test_optional_value_wins(monkeypatch):
    monkeypatch.setenv("PRUEBA_VALOR", "algo")
    assert optional("PRUEBA_VALOR", "defecto") == "algo"


def test_required_empty_raises(monkeypatch):
    monkeypatch.setenv("PRUEBA_SECRETO", "")
    with pytest.raises(MissingSecret):
        required("PRUEBA_SECRETO")
