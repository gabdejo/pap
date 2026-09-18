# tests/prices/sbs/valor_cuota/test_benchmark_composicion.py
# ---------------------------------------------------------------
# Golden tests for encadenar(): the drifting (buy-and-hold)
# composite-index chaining. No DB.
#
# Pinned down:
#   - units are struck at the rebalance (peso * I / price) and the
#     index is their mark-to-market afterwards, so weights DRIFT
#   - a rebalance re-strikes at the chained level: the index is
#     CONTINUOUS across composition changes
#   - prices forward-fill inside the grid
#   - a component with no price at the strike fails loud, by name
# ---------------------------------------------------------------

import datetime as dt

import pytest

from src.pipelines.prices.sbs.valor_cuota.benchmark_composicion import encadenar

D1, D2, D3, D4 = (dt.date(2026, 1, d) for d in (5, 6, 7, 8))


def _comp(etiqueta, peso, precios):
    return {"etiqueta": etiqueta, "peso": peso, "precios": precios}


def test_deriva_un_periodo():
    # uA = 0.6*100/10 = 6 unidades; uB = 0.4*100/20 = 2 unidades.
    niveles = dict(encadenar([{
        "desde": D1,
        "componentes": [
            _comp("A", 0.6, {D1: 10, D2: 12, D3: 11}),
            _comp("B", 0.4, {D1: 20, D2: 20, D3: 22}),
        ],
    }]))
    assert niveles[D1] == pytest.approx(100.0)
    assert niveles[D2] == pytest.approx(6 * 12 + 2 * 20)   # 112: A ya pesa mas
    assert niveles[D3] == pytest.approx(6 * 11 + 2 * 22)   # 110


def test_rebalanceo_es_continuo_y_re_estrena_pesos():
    # Cada componente llega con su serie COMPLETA (asi la arma
    # _armar_periodos desde la base), no recortada al periodo: por eso
    # la canasta vieja puede valorizarse el dia del traspaso.
    A = {D1: 10, D2: 12, D3: 11, D4: 11}
    B = {D1: 20, D2: 20, D3: 22, D4: 24.2}
    niveles = dict(encadenar([
        {"desde": D1, "componentes": [_comp("A", 0.6, A), _comp("B", 0.4, B)]},
        {"desde": D3, "componentes": [_comp("A", 0.5, A), _comp("B", 0.5, B)]},
    ]))
    assert niveles[D2] == pytest.approx(112.0)
    # D3 es el dia del rebalanceo y PERTENECE A LA CANASTA VIEJA: se
    # valoriza con ella (A 12->11, B 20->22 sobre 6 y 2 unidades = 110).
    # El rebalanceo no altera ese nivel, solo reparte 110 al 50/50; la
    # canasta nueva rige desde el cierre siguiente. Antes este dia salia
    # plano en 112 y el retorno real se perdia, una vez por rebalanceo.
    assert niveles[D3] == pytest.approx(6 * 11 + 2 * 22)      # 110
    # D4: uA = 0.5*110/11 = 5 y uB = 0.5*110/22 = 2.5; B sube 10%.
    assert niveles[D4] == pytest.approx(5 * 11 + 2.5 * 24.2)  # 115.5


def test_precio_faltante_se_arrastra():
    niveles = dict(encadenar([{
        "desde": D1,
        "componentes": [
            _comp("A", 0.5, {D1: 10, D2: 12}),
            _comp("B", 0.5, {D1: 20}),          # sin precio en D2: ffill
        ],
    }]))
    assert niveles[D2] == pytest.approx(5 * 12 + 2.5 * 20)  # B al ultimo precio


def test_sin_precio_al_estrenar_falla_con_nombre():
    with pytest.raises(ValueError, match="'B'"):
        encadenar([{
            "desde": D1,
            "componentes": [
                _comp("A", 0.5, {D1: 10}),
                _comp("B", 0.5, {D2: 20}),      # su primer precio es POSTERIOR
            ],
        }])


def test_base_configurable():
    niveles = dict(encadenar(
        [{"desde": D1, "componentes": [_comp("A", 1.0, {D1: 10, D2: 11})]}],
        base=1000.0))
    assert niveles[D1] == pytest.approx(1000.0)
    assert niveles[D2] == pytest.approx(1100.0)


def test_cambiar_de_ticker_no_rompe_la_serie():
    """
    Homologacion: sustituir un componente por otro de escala
    COMPLETAMENTE distinta no puede mover el indice el dia del cambio.

    Es la garantia que hace utilizable un benchmark de verdad, donde los
    tickers se descontinuan y se reemplazan: lo que cambia es que
    representa el indice de ahi en adelante, no su nivel. Si el
    encadenado se hiciera sobre precios en vez de sobre unidades
    re-fijadas, pasar de un ticker de 50 a uno de 7.500 multiplicaria la
    serie por 150 en un dia.
    """
    niveles = dict(encadenar([
        {"desde": D1, "componentes": [
            _comp("A", 0.5, {D1: 100.0, D2: 110.0, D3: 120.0}),
            _comp("B", 0.5, {D1: 50.0, D2: 50.0, D3: 50.0}),
        ]},
        {"desde": D3, "componentes": [
            _comp("A", 0.7, {D3: 120.0, D4: 132.0}),
            _comp("C", 0.3, {D3: 7_500.0, D4: 7_500.0}),   # reemplaza a B
        ]},
    ]))
    # D2: A +10% y B plano, sobre 0,5 y 1 unidades -> 105
    assert niveles[D2] == pytest.approx(105.0)
    # D3 es el dia del cambio: se valoriza con la canasta VIEJA (110) y
    # el reemplazo de B por C -de 50 a 7.500- no mueve el nivel.
    assert niveles[D3] == pytest.approx(0.5 * 120 + 1 * 50)   # 110
    # D4: ya con la canasta nueva, A +10% pesando 70% -> +7%
    assert niveles[D4] == pytest.approx(110.0 * 1.07)


def test_cambiar_solo_los_pesos_tampoco_salta():
    """El otro caso del enunciado: misma canasta, pesos distintos."""
    niveles = dict(encadenar([
        {"desde": D1, "componentes": [
            _comp("A", 0.5, {D1: 100.0, D2: 120.0}),
            _comp("B", 0.5, {D1: 100.0, D2: 100.0}),
        ]},
        {"desde": D2, "componentes": [
            _comp("A", 0.9, {D2: 120.0, D3: 132.0}),
            _comp("B", 0.1, {D2: 100.0, D3: 100.0}),
        ]},
    ]))
    assert niveles[D2] == pytest.approx(110.0)     # +20% y 0%, al 50/50
    # El rebalanceo del D2 no mueve el nivel; recien el D3 pesa el 90/10.
    assert niveles[D3] == pytest.approx(110.0 * (1 + 0.9 * 0.10))


# ---- La negativa a borrar una serie en uso nombra el indice -----------------

def test_el_motivo_dice_que_indice_y_que_fondo():
    from src.pipelines.prices.sbs.valor_cuota.benchmark_composicion import motivo_en_uso
    vig = [{"tipo": "target", "fondo": 2, "vigente_desde": "2026-01-01"}]
    hist = [{"tipo": "benchmark", "fondo": 3, "vigente_desde": "2020-01-01"}]
    m = motivo_en_uso("SPX Index", vig, hist)
    assert "target del fondo 2" in m
    assert "canasta vigente" in m
    m2 = motivo_en_uso("SPX Index", [], hist)
    assert "benchmark del fondo 3" in m2
    assert "ya no esta en ninguna canasta vigente" in m2
