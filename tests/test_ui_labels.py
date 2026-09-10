from uuid import UUID

import pytest

from nourish_nest.recipe_client_models import StoredFood
from nourish_nest.ui_labels import food_labels, friendly_message, humanize, unit_label


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("may_contain", "May contain"),
        ("low_stock", "Low stock"),
        ("development_fixture", "Development sample"),
    ],
)
def test_friendly_enums(raw, expected):
    assert humanize(raw) == expected


def test_duplicate_names_keep_identity_without_exposing_ids():
    foods = [
        StoredFood(id=UUID(int=1), name="Rice", source_provider="usda"),
        StoredFood(id=UUID(int=2), name="Rice"),
        StoredFood(id=UUID(int=3), name="Rice"),
    ]
    choices = food_labels(foods)
    assert choices[foods[0].id] == "Rice — USDA"
    assert choices[foods[1].id] == "Rice — Manual (option 1)"
    assert choices[foods[2].id] == "Rice — Manual (option 2)"
    assert len(set(choices.values())) == 3
    assert all(str(food.id) not in choices[food.id] for food in foods)


def test_error_redaction_and_unit_explanations():
    assert (
        friendly_message(f"Food {UUID(int=1)} unavailable")
        == "Food the selected record unavailable"
    )
    assert unit_label("kg") == "kg — kilograms"
