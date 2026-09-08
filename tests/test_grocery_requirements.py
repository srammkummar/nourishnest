from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from test_grocery_crud import grocery_client  # noqa: F401

from nourish_nest.grocery_requirement_schemas import GroceryRequirementsRequest
from nourish_nest.grocery_requirement_services import GroceryRequirementsService
from nourish_nest.models import (
    Food,
    FoodSourceType,
    GroceryList,
    GroceryListItem,
    Household,
    Recipe,
    RecipeIngredient,
)


@pytest.fixture
def preview_data(request):
    client, sessions = request.getfixturevalue("grocery_client")
    with sessions() as session:
        home, other = Household(name="Home"), Household(name="Other")
        rice = Food(name="Rice", normalized_name="rice", source_type=FoodSourceType.MANUAL,
                    serving_quantity=Decimal(1), serving_unit="g")
        session.add_all([home, other, rice])
        session.flush()

        def recipe(name, owner, servings, quantity, unit):
            row = Recipe(name=name, household_id=owner, servings=Decimal(servings))
            row.ingredients = [RecipeIngredient(food=rice, quantity=Decimal(quantity),
                                                 unit=unit, display_order=0)]
            session.add(row)
            session.flush()
            return row.id

        recipes = {
            "first": recipe("First", home.id, "2", "0.1", "kg"),
            "second": recipe("Second", home.id, "4", "200", "g"),
            "system": recipe("System", None, "3", "0.3", "kg"),
            "foreign": recipe("Private", other.id, "2", "1", "kg"),
            "volume": recipe("Volume", home.id, "2", "1", "cup"),
            "unknown": recipe("Unknown", home.id, "2", "1", "pinch"),
        }
        session.commit()
        return client, sessions, home.id, rice.id, recipes


def preview(data, selections, household_id=None):
    client, _, home_id, _, _ = data
    return client.post(
        f"/v1/households/{household_id or home_id}/grocery-requirements/preview",
        json={"recipes": [{"recipe_id": str(recipe_id), "desired_servings": servings}
                          for recipe_id, servings in selections]},
        headers={"x-request-id": "preview-1"},
    )


def test_single_recipe_scaling(preview_data):
    _, _, home, food, recipes = preview_data
    response = preview(preview_data, [(recipes["first"], 4)])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["household_id"] == str(home)
    assert body["calculation_version"] == "grocery-requirements-v1"
    assert body["recipes"] == [{"recipe_id": str(recipes["first"]), "desired_servings": "4"}]
    assert body["warnings"] == []
    assert response.headers["x-request-id"] == "preview-1"
    requirement, = body["requirements"]
    assert requirement["food_id"] == str(food)
    assert requirement["food_name"] == "Rice" and requirement["canonical_unit"] == "g"
    assert Decimal(requirement["required_quantity"]) == Decimal(200)
    source, = requirement["sources"]
    assert source["recipe_id"] == str(recipes["first"])
    assert Decimal(source["scaled_quantity"]) == Decimal("0.2")
    assert source["original_unit"] == "kg"
    assert Decimal(source["required_quantity"]) == Decimal(200)


def test_aggregation_exact_decimal_and_system_access(preview_data):
    recipes = preview_data[-1]
    selections = [(recipes["first"], "0.2"), (recipes["second"], "0.4"), (recipes["system"], 3)]
    response = preview(preview_data, selections)
    assert response.status_code == 200, response.text
    requirement, = response.json()["requirements"]
    assert Decimal(requirement["required_quantity"]) == Decimal(330)
    assert len(requirement["sources"]) == 3
    assert sum((Decimal(source["required_quantity"]) for source in requirement["sources"]),
               Decimal(0)) == Decimal(330)
    assert preview(preview_data, list(reversed(selections))).json() == response.json()


@pytest.mark.parametrize("kind", ["foreign", "missing_recipe", "missing_household"])
def test_access_errors_preserve_envelope(preview_data, kind):
    recipes = preview_data[-1]
    recipe_id = uuid4() if kind == "missing_recipe" else recipes[
        "foreign" if kind == "foreign" else "first"
    ]
    response = preview(preview_data, [(recipe_id, 4)],
                       household_id=uuid4() if kind == "missing_household" else None)
    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found", "request_id": "preview-1",
        "message": "Household not found" if kind == "missing_household" else "Recipe not found",
    }
    assert response.headers["x-request-id"] == "preview-1"


@pytest.mark.parametrize("servings", [0, -1, "NaN", "Infinity", 0.5])
def test_invalid_servings(preview_data, servings):
    response = preview(preview_data, [(preview_data[-1]["first"], servings)])
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["request_id"] == "preview-1"


def test_duplicate_and_empty_selections(preview_data):
    recipe = preview_data[-1]["first"]
    for selections in [[], [(recipe, 2), (recipe, 4)]]:
        response = preview(preview_data, selections)
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_request"


def test_incompatible_and_unsupported_units(preview_data):
    recipes = preview_data[-1]
    response = preview(preview_data, [(recipes[key], 4) for key in ["first", "volume", "unknown"]])
    assert response.status_code == 200, response.text
    requirements = response.json()["requirements"]
    assert {row["canonical_unit"]: Decimal(row["required_quantity"]) for row in requirements} == {
        "g": Decimal(200), "ml": Decimal("473.176473"),
    }
    warnings = {warning["code"]: warning for warning in response.json()["warnings"]}
    assert set(warnings) == {"incompatible_units", "unsupported_conversion"}
    assert warnings["incompatible_units"]["canonical_units"] == ["g", "ml"]
    unsupported = warnings["unsupported_conversion"]
    assert unsupported["recipe_id"] == str(recipes["unknown"])
    assert unsupported["ingredient_id"]
    assert unsupported["original_unit"] == "pinch"
    assert Decimal(unsupported["scaled_quantity"]) == Decimal(2)


def test_deterministic_food_order_and_all_ingredients(preview_data):
    _, sessions, home, _, recipes = preview_data
    with sessions() as session:
        recipe = session.get(Recipe, recipes["first"])
        for name in ["Zucchini", "Apple", "Apple"]:
            food = Food(name=name, normalized_name=name.lower(), source_type=FoodSourceType.MANUAL,
                        serving_quantity=Decimal(1), serving_unit="item")
            recipe.ingredients.append(RecipeIngredient(food=food, quantity=Decimal("0.1"),
                                                       unit="items", display_order=1))
        session.commit()
    selections = [(recipes["first"], 4)]
    body = preview(preview_data, selections).json()
    requirements = body["requirements"]
    keys = [(row["food_name"].casefold(), row["food_id"]) for row in requirements]
    assert keys == sorted(keys)
    assert len(requirements) == 4
    assert all(Decimal(row["required_quantity"]) == Decimal("0.2")
               for row in requirements if row["canonical_unit"] == "item")
    assert preview(preview_data, selections, home).json() == body


def test_preview_never_writes_or_autoflushes(preview_data):
    _, sessions, home, _, recipes = preview_data
    with sessions() as session:
        engine = session.get_bind()
        statements = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement.strip().split()[0].upper())

        event.listen(engine, "before_cursor_execute", capture)
        try:
            session.add(GroceryList(household_id=home, name="Pending"))
            service = GroceryRequirementsService(session)
            data = GroceryRequirementsRequest(recipes=[{
                "recipe_id": recipes["first"], "desired_servings": 4,
            }])
            assert service.preview(home, data).requirements
            assert len(session.new) == 1
            assert statements and set(statements) == {"SELECT"}
        finally:
            event.remove(engine, "before_cursor_execute", capture)
            session.rollback()
        assert session.scalar(select(GroceryList)) is None
        assert session.scalar(select(GroceryListItem)) is None
