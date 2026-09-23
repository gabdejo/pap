"""
La corrida programada: que dia espera encontrar publicado.

La SBS publica el valor cuota con dos dias habiles de rezago. La tarea
de Windows dispara a las 16:00 y reintenta a las 16:30 y 17:00; antes
de abrir Chrome cada intento pregunta si el libro ya tiene ese dia. La
pregunta empieza por saber cual es el dia: aqui se fija.
"""
import datetime as dt

from src.pipelines.prices.sbs.valor_cuota.run import fecha_objetivo


def test_entre_semana_son_dos_dias_habiles_atras():
    # Miercoles 23/09/2026 -> lunes 21/09
    assert fecha_objetivo(dt.date(2026, 9, 23)) == dt.date(2026, 9, 21)


def test_el_lunes_y_el_martes_saltan_el_fin_de_semana():
    # Lunes 21/09 -> jueves 17/09; martes 22/09 -> viernes 18/09
    assert fecha_objetivo(dt.date(2026, 9, 21)) == dt.date(2026, 9, 17)
    assert fecha_objetivo(dt.date(2026, 9, 22)) == dt.date(2026, 9, 18)


def test_en_fin_de_semana_se_cuenta_desde_el_viernes():
    # Sabado 26/09 -> jueves 24/09 (viernes y jueves son los dos habiles atras)
    assert fecha_objetivo(dt.date(2026, 9, 26)) == dt.date(2026, 9, 24)
