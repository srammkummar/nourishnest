from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from test_grocery_crud import grocery_client  # noqa: F401
from test_grocery_requirements import preview_data  # noqa: F401
from test_grocery_shortage import add_lot

from nourish_nest.models import (
    Base,
    Food,
    FoodAllergen,
    FoodDietaryTagRecord,
    Household,
    PantryItemStatus,
    Recipe,
    RecipeIngredient,
)
from nourish_nest.planning_contracts import RecommendationRequest
from nourish_nest.planning_services import RecommendationService

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


@pytest.fixture
def planning_data(request, monkeypatch):
    dataset = request.getfixturevalue("preview_data")
    monkeypatch.setattr("nourish_nest.planning_services.utc_now", lambda: NOW)
    monkeypatch.setattr("nourish_nest.grocery_shortage_services.utc_now", lambda: NOW)
    with dataset[1]() as session:
        food = session.get(Food, dataset[3])
        food.calories_per_serving = Decimal(2)
        food.protein_g = Decimal("0.1")
        food.carbohydrate_g = Decimal("0.2")
        food.fat_g = Decimal("0.3")
        session.commit()
    return dataset


def recommend(data, household=None, **fields):
    return data[0].post(
        f"/v1/households/{household or data[2]}/recipe-recommendations",
        json=fields,
        headers={"x-request-id": "planning-trace"},
    )


def first(data, **fields):
    response = recommend(data, **fields)
    assert response.status_code == 200, response.text
    return next(row for row in response.json()["recommendations"] if row["recipe_name"] == "First")


def member(data, household=None, **fields):
    response = data[0].post(
        f"/v1/households/{household or data[2]}/members",
        json={
            "name": "Alex",
            "age": 35,
            "sex": "female",
            "height_cm": 165,
            "weight_kg": 68,
            "activity_level": "moderate",
            "goal": "maintain",
            "weekly_goal_kg": 0,
            "meals_per_day": 3,
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.parametrize(
    "quantity,coverage,missing",
    [("100", "100", "0"), ("25", "25", "75"), ("0", "0", "100"), ("200", "100", "0")],
)
def test_quantity_coverage(planning_data, quantity, coverage, missing):
    add_lot(planning_data, quantity)
    row = first(planning_data)
    assert Decimal(row["coverage_percentage"]) == Decimal(coverage)
    assert Decimal(row["requirements"][0]["missing_quantity"]) == Decimal(missing)
    assert row["classification"] == (
        "Ready to make" if missing == "0" else "Missing 1–2 ingredients"
    )
    assert Decimal(row["score"]["coverage_points"]) == Decimal(coverage) * Decimal("0.8")
    assert row["servings"] == "2.00"
    assert Decimal(row["nutrition_per_serving"]["calories"]) == 100


@pytest.mark.parametrize(
    "status,expiration",
    [
        (PantryItemStatus.DEPLETED, None),
        (PantryItemStatus.DISCARDED, None),
        (PantryItemStatus.EXPIRED, None),
        (PantryItemStatus.ACTIVE, NOW.date() - timedelta(days=1)),
    ],
)
def test_excludes_unusable_lots(planning_data, status, expiration):
    add_lot(planning_data, "100", status=status, expiration=expiration)
    assert Decimal(first(planning_data)["coverage_percentage"]) == 0


def test_priority_uses_expiring_quantity_and_deterministic_order(planning_data):
    add_lot(planning_data, "100")
    with planning_data[1]() as session:
        food = Food(
            name="Beans",
            normalized_name="beans",
            source_type="manual",
            serving_quantity=Decimal(1),
            serving_unit="g",
        )
        recipe = Recipe(name="Z use soon", household_id=planning_data[2], servings=Decimal(1))
        recipe.ingredients = [
            RecipeIngredient(food=food, quantity=Decimal(10), unit="g", display_order=0)
        ]
        session.add(recipe)
        session.commit()
        food_id = food.id
    bean_data = (*planning_data[:3], food_id, planning_data[-1])
    add_lot(bean_data, "10", expiration=NOW.date() + timedelta(days=1))
    first_response = recommend(planning_data)
    assert first_response.json() == recommend(planning_data).json()
    top = first_response.json()["recommendations"][0]
    assert top["recipe_name"] == "Z use soon"
    assert Decimal(top["score"]["total"]) == 100
    assert top["expiring_ingredients"][0]["food_name"] == "Beans"
    assert Decimal(top["expiring_ingredients"][0]["quantity"]) == 10


def test_allergens_checked_even_with_unsupported_units(planning_data):
    person = member(planning_data, allergies=[{"allergen": " MILK ", "severity": "severe"}])
    with planning_data[1]() as session:
        session.add(
            FoodAllergen(food_id=planning_data[3], allergen="milk", relationship_type="contains")
        )
        session.commit()
    response = recommend(planning_data, member_id=person)
    assert response.status_code == 200
    assert response.json()["recommendations"] == []
    assert recommend(planning_data).json()["recommendations"]


def test_may_contain_warns_without_exclusion(planning_data):
    person = member(planning_data, allergies=[{"allergen": "Milk", "severity": "severe"}])
    with planning_data[1]() as session:
        session.add(
            FoodAllergen(food_id=planning_data[3], allergen="Milk", relationship_type="may_contain")
        )
        session.commit()
    row = first(planning_data, member_id=person)
    assert any(w["code"] == "may_contain" and "Milk" in w["message"] for w in row["warnings"])


def test_dietary_tags_and_custom_warning(planning_data):
    person = member(
        planning_data, dietary_preferences=[{"preference_type": "vegan", "value": "Vegan"}]
    )
    assert recommend(planning_data, member_id=person).json()["recommendations"] == []
    with planning_data[1]() as session:
        session.add(FoodDietaryTagRecord(food_id=planning_data[3], tag="vegan"))
        session.commit()
    assert first(planning_data, member_id=person)
    custom = member(
        planning_data, dietary_preferences=[{"preference_type": "custom", "value": "Less salt"}]
    )
    assert any(
        w["code"] == "unverified_dietary_preference"
        for w in first(planning_data, member_id=custom)["warnings"]
    )


def test_isolation_validation_and_envelope(planning_data):
    with planning_data[1]() as session:
        other = Household(name="Other member home")
        session.add(other)
        session.commit()
        other_id = other.id
    foreign = member(planning_data, household=other_id)
    add_lot(planning_data, "1000", household=other_id)
    assert Decimal(first(planning_data)["coverage_percentage"]) == 0
    names = {row["recipe_name"] for row in recommend(planning_data).json()["recommendations"]}
    assert "System" in names and "Private" not in names
    for arguments in ({"member_id": foreign}, {"member_id": str(uuid4())}, {"household": uuid4()}):
        response = recommend(planning_data, **arguments)
        assert response.status_code == 404
        assert response.json()["request_id"] == "planning-trace"
        assert set(response.json()) == {"code", "message", "request_id"}
    assert recommend(planning_data, maximum_missing_ingredients=-1).status_code == 422
    assert recommend(planning_data, limit=0).status_code == 422


def test_unsupported_and_incompatible_conversions_are_not_ready(planning_data):
    add_lot(planning_data, "10", "l")
    add_lot(planning_data, "1", "handful")
    rows = recommend(planning_data).json()["recommendations"]
    unknown = next(row for row in rows if row["recipe_name"] == "Unknown")
    assert unknown["classification"] != "Ready to make"
    assert Decimal(unknown["coverage_percentage"]) == 0
    assert unknown["missing_ingredients"][0]["unit"] == "pinch"
    assert not unknown["missing_ingredients"][0]["conversion_supported"]
    assert unknown["nutrition_per_serving"]["calories"] is None
    assert any(w["code"] == "unsupported_conversion" for w in unknown["warnings"])
    assert {w["code"] for w in first(planning_data)["warnings"]} >= {
        "incompatible_pantry_units",
        "unsupported_pantry_conversion",
    }


def test_filters_and_no_database_writes(planning_data):
    add_lot(planning_data, "100")
    with planning_data[1]() as session:
        recipe = session.get(Recipe, planning_data[-1]["first"])
        recipe.cuisine = "Italian"
        recipe.cooking_minutes = 20
        session.commit()
    assert (
        len(
            recommend(planning_data, cuisine="italian", maximum_cooking_minutes=20).json()[
                "recommendations"
            ]
        )
        == 1
    )
    assert (
        recommend(planning_data, cuisine="italian", maximum_cooking_minutes=19).json()[
            "recommendations"
        ]
        == []
    )
    assert all(
        r["classification"] == "Ready to make"
        for r in recommend(planning_data, maximum_missing_ingredients=0).json()["recommendations"]
    )
    with planning_data[1]() as session:
        before = {t.name: session.execute(select(t)).all() for t in Base.metadata.sorted_tables}
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.upper())

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            pending = Household(name="Must not flush")
            session.add(pending)
            RecommendationService(session).recommend(planning_data[2], RecommendationRequest())
            assert pending in session.new
            session.expunge(pending)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert not any(
            s.startswith(("INSERT", "UPDATE", "DELETE")) or "FOR UPDATE" in s for s in statements
        )
        assert before == {
            t.name: session.execute(select(t)).all() for t in Base.metadata.sorted_tables
        }


def test_needs_shopping_counts_distinct_foods(planning_data):
    with planning_data[1]() as session:
        recipe = session.get(Recipe, planning_data[-1]["first"])
        for index in range(2):
            food = Food(
                name=f"Extra {index}",
                normalized_name=f"extra {index}",
                source_type="manual",
                serving_quantity=Decimal(1),
                serving_unit="g",
            )
            recipe.ingredients.append(
                RecipeIngredient(food=food, quantity=Decimal(1), unit="g", display_order=index + 1)
            )
        session.commit()
    row = first(planning_data)
    assert row["classification"] == "Needs shopping" and row["missing_ingredient_count"] == 3
    assert "First" not in {
        r["recipe_name"]
        for r in recommend(planning_data, maximum_missing_ingredients=2).json()["recommendations"]
    }
