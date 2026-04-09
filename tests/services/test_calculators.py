from bot.services.accident_travel import AccidentTravelInput, calculate_accident_travel
from bot.services.expeditor import parse_plan_choice
from bot.services.generic_calc import GenericInput, calculate_generic
from bot.services.kasko import KaskoInput, calculate_kasko
from bot.services.property import PropertyInput, calculate_property


def test_calculate_kasko_applies_minimum_premium() -> None:
    inp = KaskoInput(
        brand_model="VW Polo",
        year=2025,
        car_value=1_000.0,
        abroad=False,
        drivers_count=1,
        youngest_driver_age=35,
    )
    quote = calculate_kasko(inp)
    assert quote.currency == "BYN"
    assert quote.premium == 150.0
    assert any("Базовая ставка" in row[0] for row in quote.breakdown)


def test_calculate_generic_uses_risk_keywords() -> None:
    inp = GenericInput(
        full_name="Иван Иванов",
        contact="+375291112233",
        subject="Груз",
        insured_value=10_000.0,
        comment="Срочно и высокий риск",
    )
    quote = calculate_generic("cargo", inp)
    # 10000 * 0.0035 * 1.10 = 38.5 -> floor to minimum cargo premium 50
    assert quote.premium == 50.0
    assert quote.currency == "BYN"


def test_calculate_property_location_and_risk_factors() -> None:
    inp = PropertyInput(
        full_name="Петр Петров",
        contact="+375291112244",
        subject="Квартира",
        address_or_city="г. Минск",
        property_value=100_000.0,
        comment="Риск пожар",
    )
    quote = calculate_property(inp)
    # 100000 * 0.0025 * 1.10 * 1.05 = 288.75
    assert quote.premium == 288.75
    assert quote.currency == "BYN"


def test_calculate_accident_travel_variant_a() -> None:
    inp = AccidentTravelInput(
        full_name="Иван Иванов",
        contact="+375291112255",
        days=10,
        age=30,
        variant_a=True,
        variant_b=False,
        sum_a=30000,
        sum_b=None,
        territory_option=1,
        sport_training=False,
        insured_count=1,
        repeat_contract=False,
        e_policy=False,
    )
    quote = calculate_accident_travel(inp)
    assert quote.currency == "USD/EUR"
    # For 10 days and 30000 in table A base is 4.0, all coefs are 1.0
    assert quote.premium == 4.0


def test_calculate_accident_travel_requires_variant() -> None:
    inp = AccidentTravelInput(
        full_name="Иван Иванов",
        contact="+375291112266",
        days=10,
        age=30,
        variant_a=False,
        variant_b=False,
        sum_a=None,
        sum_b=None,
        territory_option=1,
        sport_training=False,
        insured_count=1,
        repeat_contract=False,
        e_policy=False,
    )
    try:
        calculate_accident_travel(inp)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "хотя бы один вариант" in str(exc)


def test_parse_plan_choice_aliases() -> None:
    assert parse_plan_choice("1").key == "base"
    assert parse_plan_choice("премиум").key == "premium"
    assert parse_plan_choice("unknown") is None
