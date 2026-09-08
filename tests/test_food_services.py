from decimal import Decimal

import pytest

from nourish_nest.food_services import UnsupportedConversionError, convert_quantity


@pytest.mark.parametrize(
    ("quantity", "unit", "expected", "canonical"),
    [
        ("1", "g", "1", "g"),
        ("1", "kg", "1000", "g"),
        ("1", "oz", "28.349523125", "g"),
        ("1", "lb", "453.59237", "g"),
        ("1", "ml", "1", "ml"),
        ("1", "l", "1000", "ml"),
        ("1", "cup", "236.5882365", "ml"),
        ("1", "tablespoon", "14.7867648", "ml"),
        ("1", "teaspoon", "4.92892159", "ml"),
        ("2", "items", "2", "item"),
    ],
)
def test_supported_units_convert_to_canonical_dimensions(quantity, unit, expected, canonical):
    result = convert_quantity(Decimal(quantity), unit)
    assert result.quantity == Decimal(expected)
    assert result.canonical_unit == canonical


def test_unknown_unit_raises_structured_conversion_error():
    with pytest.raises(UnsupportedConversionError) as error:
        convert_quantity(Decimal(1), "pinch")
    assert error.value.code == "unsupported_conversion"