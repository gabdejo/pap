# tests/prices/sbs/valor_cuota/test_afps.py
# ---------------------------------------------------------------
# Pins down the AFP registry contract (config/afps.yaml):
#   - alias resolution folds accents, case and the 'AFP ' prefix,
#     so a corporate rename never orphans historical data
#   - procode naming is derived from the key, never the name, and
#     parsing only recognizes registered AFP funds
#   - exactly one AFP declares itself the house
#   - the metric -> field -> domain maps are total, and only the NAV
#     is a price
# No DB.
# ---------------------------------------------------------------

from src.pipelines.prices.sbs.valor_cuota import afps


def test_key_resolves_name_alias_and_accents():
    assert afps.key_of("HABITAT") == "habitat"
    assert afps.key_of("AFP HÁBITAT") == "habitat"
    assert afps.key_of("habitat") == "habitat"
    assert afps.key_of("  afp  habitat ") == "habitat"


def test_unknown_afp_resolves_to_none():
    assert afps.key_of("AFP INEXISTENTE") is None


def test_procode_is_key_based():
    assert afps.procode("AFP PROFUTURO", 2) == "SPP_PROFUTURO_F2"
    assert afps.procode("profuturo", 0) == "SPP_PROFUTURO_F0"


def test_procode_roundtrips_through_parse():
    code = afps.procode("INTEGRA", 3)
    assert afps.parse_procode(code) == ("integra", 3)


def test_parse_procode_only_recognizes_registered_funds():
    # Other SPP_ entities share the prefix (the analytics layer's composite
    # indices, for instance) and must not be mistaken for an AFP fund.
    assert afps.parse_procode("SPP_TARGET_F2") is None
    assert afps.parse_procode("SPP_BENCH_F1") is None
    assert afps.parse_procode("SBS_HABITAT_F2") is None
    assert afps.parse_procode("SPP_HABITAT_X") is None


def test_exactly_one_house_afp():
    houses = [a for a in afps.afps() if a.get("house")]
    assert len(houses) == 1
    assert afps.house_afp() == houses[0]["name"]


def test_metric_field_map_is_total():
    assert set(afps.METRIC_FIELD) == {"valor_cuota", "cuotas", "fondo"}
    assert set(afps.FIELD_METRIC) == {"PX_LAST", "CUOTAS", "FONDO_SOLES"}


def test_only_the_nav_is_a_price():
    assert set(afps.FIELD_DOMAIN) == set(afps.FIELD_METRIC)
    assert afps.FIELD_DOMAIN["PX_LAST"] == "prices"
    assert afps.FIELD_DOMAIN["CUOTAS"] == "fundamentals"
    assert afps.FIELD_DOMAIN["FONDO_SOLES"] == "fundamentals"
