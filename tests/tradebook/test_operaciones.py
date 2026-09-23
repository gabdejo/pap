"""
El Tradebook: lo que decide si una fila entra al libro y como.

No se prueba la aritmetica sino los sitios donde equivocarse cuesta: el
lado (que invierte el signo de la operacion), el monto deducido (que no
puede deducirse al reves), y la normalizacion de lo que viene escrito de
cualquier manera en un Excel de mesa.
"""
import datetime as dt

import pytest

from src.pipelines.tradebook.operaciones import (_validar, normalizar_lado,
                                                 normalizar_moneda)


# ---- El lado ---------------------------------------------------------------

@pytest.mark.parametrize("crudo", ["compra", "COMPRA", " Compra ", "C", "c",
                                   "buy", "BUY", "B", "adquisicion"])
def test_las_formas_de_decir_compra(crudo):
    assert normalizar_lado(crudo) == "compra"


@pytest.mark.parametrize("crudo", ["venta", "VENTA", "V", "sell", "SELL", "S",
                                   "sold"])
def test_las_formas_de_decir_venta(crudo):
    assert normalizar_lado(crudo) == "venta"


@pytest.mark.parametrize("crudo", ["permuta", "swap", "", None, "reporte", "x"])
def test_un_lado_que_no_se_reconoce_se_rechaza_en_vez_de_suponerse(crudo):
    """
    Adivinar el lado invierte el signo de la operacion. Es preferible que
    la fila caiga y se vea en las descartadas a que entre como lo
    contrario de lo que fue.
    """
    with pytest.raises(ValueError, match="lado"):
        normalizar_lado(crudo)


# ---- La moneda -------------------------------------------------------------

@pytest.mark.parametrize("crudo, esperado", [
    ("PEN", "PEN"), ("pen", "PEN"), ("S/", "PEN"), ("S/.", "PEN"),
    ("soles", "PEN"), ("SOLES", "PEN"),
    ("USD", "USD"), ("usd", "USD"), ("dolares", "USD"), ("US$", "USD"),
    ("EUR", "EUR"),
    ("", "PEN"), (None, "PEN"),          # vacio = moneda de la casa
])
def test_normalizacion_de_moneda(crudo, esperado):
    assert normalizar_moneda(crudo) == esperado


# ---- La operacion completa -------------------------------------------------

BASE = {"fecha": "2026-09-10", "fondo": 2, "lado": "compra",
        "instrumento": "PERU 3.55 03/31", "cantidad": 1000, "precio": 98.45,
        "moneda": "USD", "trader": "J. PEREZ"}


def test_el_monto_se_deduce_de_cantidad_por_precio():
    op = _validar(dict(BASE))
    assert op["monto"] == pytest.approx(1000 * 98.45)


def test_el_monto_que_viene_manda_sobre_el_calculado():
    """
    El monto del sistema origen lleva comisiones y devengado dentro.
    Recalcularlo restataria en silencio lo que la mesa pago de verdad.
    """
    op = _validar({**BASE, "monto": 98_600})
    assert op["monto"] == 98_600


def test_sin_monto_ni_precio_no_hay_operacion():
    sin = {k: v for k, v in BASE.items() if k != "precio"}
    with pytest.raises(ValueError, match="monto"):
        _validar(sin)


def test_la_cantidad_negativa_se_rechaza():
    """
    La direccion la dice el lado. Una cantidad negativa en una venta
    significaria la venta dos veces, y cada lector tendria que decidir a
    cual de las dos hacer caso.
    """
    with pytest.raises(ValueError, match="mayor que cero"):
        _validar({**BASE, "cantidad": -1000})


def test_la_cantidad_cero_tampoco():
    with pytest.raises(ValueError, match="mayor que cero"):
        _validar({**BASE, "cantidad": 0})


def test_las_fechas_llegan_como_fecha_no_como_texto():
    op = _validar(dict(BASE))
    assert isinstance(op["fecha"], dt.date)


@pytest.mark.parametrize("crudo", ["10/09/2026", "2026-09-10", "10-09-2026"])
def test_formatos_de_fecha_que_usa_una_mesa(crudo):
    assert _validar({**BASE, "fecha": crudo})["fecha"] == dt.date(2026, 9, 10)


def test_el_instrumento_vacio_se_rechaza():
    with pytest.raises(ValueError, match="instrumento"):
        _validar({**BASE, "instrumento": "   "})


def test_un_origen_inventado_se_rechaza():
    """La columna dice de donde salio la fila; un valor libre la haria inutil."""
    with pytest.raises(ValueError, match="Origen"):
        _validar({**BASE, "origen": "intuicion"})


def test_el_origen_por_defecto_es_manual():
    assert _validar(dict(BASE))["origen"] == "manual"


def test_la_referencia_vacia_queda_en_nulo_y_no_en_cadena():
    """
    El indice unico es parcial sobre referencia IS NOT NULL. Una cadena
    vacia no es nula, asi que dos filas sin referencia chocarian entre si
    y la segunda carga fallaria.
    """
    assert _validar({**BASE, "referencia": ""})["referencia"] is None
    assert _validar({**BASE, "referencia": "   "})["referencia"] is None
    assert _validar({**BASE, "referencia": "OP1"})["referencia"] == "OP1"


# ---- Los dos libros: quien registro la operacion ---------------------------

SIN_TRADER = {k: v for k, v in BASE.items() if k != "trader"}


@pytest.mark.parametrize("origen", ["manual", "excel"])
def test_el_registro_propio_exige_trader(origen):
    """
    Es la razon de ser de este libro al lado del de FMS. Sin el, la vista
    por trader queda con agujeros y reparte mal sin que nada falle.
    """
    with pytest.raises(ValueError, match="trader"):
        _validar({**SIN_TRADER, "origen": origen})


@pytest.mark.parametrize("vacio", ["", "   ", None])
def test_un_trader_en_blanco_no_cuenta_como_trader(vacio):
    with pytest.raises(ValueError, match="trader"):
        _validar({**BASE, "trader": vacio, "origen": "manual"})


def test_fms_no_lleva_trader():
    op = _validar({**SIN_TRADER, "origen": "fms"})
    assert op["origen"] == "fms"
    assert op["trader"] is None


def test_una_fila_de_fms_con_trader_se_rechaza():
    """
    FMS no dice quien opero. Escribirlo ahi seria una atribucion
    inventada, y bastaria una para que el reparto por trader dejara de
    poder creerse.
    """
    with pytest.raises(ValueError, match="FMS"):
        _validar({**BASE, "origen": "fms"})


def test_el_trader_se_guarda_limpio_de_espacios():
    assert _validar({**BASE, "trader": "  J. PEREZ  "})["trader"] == "J. PEREZ"


def test_posiciones_ya_no_es_un_origen():
    """La derivacion desde tenencias se retiro; el valor no debe volver."""
    with pytest.raises(ValueError, match="Origen"):
        _validar({**BASE, "origen": "posiciones"})


# ---- Lo que la captura A MANO exige de mas ---------------------------------

A_MANO = {**BASE, "origen": "manual"}


def test_a_mano_el_precio_es_obligatorio():
    sin = {k: v for k, v in A_MANO.items() if k != "precio"}
    with pytest.raises(ValueError, match="el precio"):
        _validar({**sin, "monto": 98_450})


def test_a_mano_la_moneda_es_obligatoria():
    """
    La moneda vacia NO cae en PEN cuando se captura a mano. En un archivo
    ese valor por defecto es comodo; a mano es una trampa: un descuido
    convierte una operacion en dolares en una en soles, y el numero queda
    perfectamente plausible.
    """
    sin = {k: v for k, v in A_MANO.items() if k != "moneda"}
    with pytest.raises(ValueError, match="la moneda"):
        _validar(sin)


@pytest.mark.parametrize("vacio", ["", "   ", None])
def test_a_mano_la_moneda_en_blanco_no_vale(vacio):
    with pytest.raises(ValueError, match="la moneda"):
        _validar({**A_MANO, "moneda": vacio})


def test_a_mano_los_dos_que_faltan_se_nombran_juntos():
    sin = {k: v for k, v in A_MANO.items() if k not in ("precio", "moneda")}
    with pytest.raises(ValueError, match="el precio y la moneda"):
        _validar({**sin, "monto": 98_450})


def test_a_mano_con_los_dos_pasa():
    op = _validar(A_MANO)
    assert op["precio"] == 98.45 and op["moneda"] == "USD"


@pytest.mark.parametrize("origen", ["excel", "fms"])
def test_por_archivo_siguen_siendo_opcionales(origen):
    """
    Lo exigido de mas es de la captura a mano, no del libro. Un archivo
    puede traer solo el monto, porque el sistema que lo genero a veces no
    da el precio, y endurecerlo aqui romperia cargas que hoy funcionan.
    """
    sin = {k: v for k, v in BASE.items() if k not in ("precio", "moneda")}
    if origen == "fms":
        sin.pop("trader", None)
    op = _validar({**sin, "monto": 98_450, "origen": origen})
    assert op["precio"] is None
    assert op["moneda"] == "PEN"      # ahi si se rellena sola


def test_la_columna_del_trader_se_llama_book_y_acepta_los_dos_nombres():
    """Lo que el formulario llama Book es la columna `book` del Excel;
    los archivos que aun digan `trader` siguen entrando."""
    from src.pipelines.tradebook.operaciones import _ALIAS, encabezado
    assert encabezado("trader") == "book"
    assert "book" in _ALIAS["trader"] and "trader" in _ALIAS["trader"]
    assert "fecha_liquidacion" not in _ALIAS


def test_el_operador_es_un_campo_propio_y_ya_no_cae_en_el_book():
    """Una columna `operador` del Excel llena operador, no book; y sin
    operador la operacion entra igual, porque es opcional."""
    from src.pipelines.tradebook.operaciones import _ALIAS
    assert "operador" in _ALIAS and "operador" not in _ALIAS["trader"]
    con = _validar({**BASE, "operador": "  M. LOPEZ "})
    assert con["operador"] == "M. LOPEZ" and con["trader"] == "J. PEREZ"
    assert _validar(BASE)["operador"] is None
