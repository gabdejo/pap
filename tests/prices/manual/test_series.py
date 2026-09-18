# tests/prices/manual/test_series.py
# ---------------------------------------------------------------
# The DB-free half of the manual series store: the tolerant file
# reader. The write paths run against the database and are covered
# by the end-to-end exercise, not here.
# ---------------------------------------------------------------

import pytest

from src.pipelines.prices.manual.series import leer_archivo_valores


def test_csv_basico_fecha_valor():
    csv = b"fecha,valor\n2026-08-20,128.45\n2026-08-21,129.10\n"
    lec = leer_archivo_valores(csv)
    assert lec["filas"] == 2
    assert lec["desde"] == "2026-08-20" and lec["hasta"] == "2026-08-21"
    import datetime as dt
    assert lec["valores"][dt.date(2026, 8, 20)] == pytest.approx(128.45)


def test_columna_valor_por_sinonimo_y_unica_sin_nombre():
    # 'precio' is a named synonym...
    lec = leer_archivo_valores(b"fecha,precio\n2026-08-20,10\n")
    assert lec["filas"] == 1
    # ...and a single unnamed second column is taken with a warning.
    lec = leer_archivo_valores(b"fecha,mi_indice\n2026-08-20,10\n")
    assert lec["filas"] == 1
    assert any("mi_indice" in a for a in lec["avisos"])


def test_dos_columnas_sin_nombre_conocido_se_rechaza():
    with pytest.raises(ValueError, match="columna del valor"):
        leer_archivo_valores(b"fecha,a,b\n2026-08-20,1,2\n")


def test_omite_futuras_negativas_e_ilegibles_y_repite_manda_ultima():
    csv = (b"fecha,valor\n"
           b"2030-01-01,10\n"        # future
           b"2026-08-20,-5\n"        # not positive
           b"garabato,10\n"          # unreadable date
           b"2026-08-21,100\n"
           b"2026-08-21,200\n")      # repeat: last wins
    lec = leer_archivo_valores(csv)
    assert lec["total_omitidas"] == 3
    assert lec["filas"] == 1
    import datetime as dt
    assert lec["valores"][dt.date(2026, 8, 21)] == pytest.approx(200)
    assert any("repetid" in a for a in lec["avisos"])


def test_decimal_coma_y_fecha_ddmm():
    csv = "fecha;valor\n20/08/2026;1.234,56\n".encode()
    lec = leer_archivo_valores(csv)
    import datetime as dt
    assert lec["valores"][dt.date(2026, 8, 20)] == pytest.approx(1234.56)


def test_archivo_sin_nada_valido_falla_claro():
    with pytest.raises(ValueError, match="fecha"):
        leer_archivo_valores(b"cualquier,cosa\n1,2\n")
