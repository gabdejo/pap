"""
La ventana del ejercicio (FY), que va de 31/10 a 31/10.

Lo que se prueba no es la aritmetica sino el limite: el 31/10 pertenece
al ejercicio que TERMINA en el, y ese dia es a la vez la base del
siguiente. Equivocarlo devuelve un rendimiento de cero justo el dia del
cierre anual, que es cuando mas se mira.
"""
import datetime as dt

import pytest

from web.api.services.spp import _inicio_fy, fechas_referencia


@pytest.mark.parametrize("hoy, inicio_esperado", [
    # En medio del ejercicio: arranco el 31/10 del año anterior.
    (dt.date(2026, 9, 15), dt.date(2025, 11, 1)),
    (dt.date(2026, 1, 2),  dt.date(2025, 11, 1)),
    # El dia del cierre: sigue perteneciendo al ejercicio que termina.
    (dt.date(2026, 10, 31), dt.date(2025, 11, 1)),
    # El dia siguiente ya es el ejercicio nuevo.
    (dt.date(2026, 11, 1), dt.date(2026, 11, 1)),
    (dt.date(2026, 12, 31), dt.date(2026, 11, 1)),
])
def test_inicio_del_ejercicio(hoy, inicio_esperado):
    assert _inicio_fy(hoy) == inicio_esperado


def test_la_base_es_el_cierre_del_31_de_octubre():
    """La base es un dia de COTIZACION, no el 31/10 del calendario."""
    fechas = [dt.date(2025, 10, 29), dt.date(2025, 10, 30), dt.date(2025, 10, 31),
              dt.date(2025, 11, 3), dt.date(2026, 9, 14), dt.date(2026, 9, 15)]
    ref = fechas_referencia(fechas, dt.date(2026, 9, 15))
    assert ref["fy"] == dt.date(2025, 10, 31)


def test_si_el_31_no_cotiza_la_base_es_el_ultimo_dia_de_octubre():
    """2026: el 31/10 cae sabado. La base baja al viernes 30."""
    fechas = [dt.date(2026, 10, 29), dt.date(2026, 10, 30),
              dt.date(2026, 11, 2), dt.date(2026, 11, 3)]
    ref = fechas_referencia(fechas, dt.date(2026, 11, 3))
    assert ref["fy"] == dt.date(2026, 10, 30)


def test_el_dia_del_cierre_no_se_mide_contra_si_mismo():
    """
    Parado en el 31/10, la base tiene que ser el 31/10 ANTERIOR. Si se
    tomara el mismo dia, el ejercicio marcaria 0.00% el unico dia en que
    su resultado esta completo.
    """
    fechas = [dt.date(2025, 10, 31), dt.date(2026, 6, 1), dt.date(2026, 10, 31)]
    ref = fechas_referencia(fechas, dt.date(2026, 10, 31))
    assert ref["fy"] == dt.date(2025, 10, 31)


def test_sin_historia_suficiente_la_base_queda_vacia():
    """Sin cierre antes del 31/10 no hay ventana, y eso se dice con None."""
    fechas = [dt.date(2026, 6, 1), dt.date(2026, 9, 15)]
    ref = fechas_referencia(fechas, dt.date(2026, 9, 15))
    assert ref["fy"] is None
