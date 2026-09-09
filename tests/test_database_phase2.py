from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from nourish_nest.api import app
from nourish_nest.database import Base, get_db
from nourish_nest.models import Recipe, RecipeIngredient, RecipeInstruction


@pytest.fixture
def phase2_client(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'phase2.db'}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, session_factory
    app.dependency_overrides.clear()
    engine.dispose()


def food_payload(name: str = "Brown Rice", **overrides) -> dict:
    payload = {
        "name": name,
        "description": "Test food",
        "source_type": "manual",
        "serving_quantity": 100,
        "serving_unit": "g",
        "grams_per_serving": 100,
        "calories_per_serving": 130,
        "protein_g": 3,
        "carbohydrate_g": 28,
        "fat_g": 1,
        "fiber_g": 2,
        "sugar_g": 1,
        "sodium_mg": 2,
        "allergens": [],
        "dietary_tags": [{"tag": "vegetarian"}],
    }
    payload.update(overrides)
    return payload


def create_food(client: TestClient, name: str = "Brown Rice", **overrides) -> dict:
    response = client.post("/v1/foods", json=food_payload(name, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def create_household(client: TestClient) -> str:
    response = client.post("/v1/households", json={"name": "Test home"})
    assert response.status_code == 201
    return response.json()["id"]


def recipe_payload(food_ids: list[str], servings: int = 2, units: list[str] | None = None) -> dict:
    units = units or ["g"] * len(food_ids)
    return {
        "name": "Test recipe",
        "description": "A deterministic test recipe",
        "cuisine": "Indian-inspired",
        "preparation_minutes": 10,
        "cooking_minutes": 20,
        "servings": servings,
        "source": "test",
        "ingredients": [
            {
                "food_id": food_id,
                "quantity": 100,
                "unit": unit,
                "preparation_note": None,
                "display_order": index,
            }
            for index, (food_id, unit) in enumerate(zip(food_ids, units, strict=True))
        ],
        "instructions": [{"step_number": 1, "instruction": "Combine and cook."}],
    }


def test_food_crud_and_normalized_search(phase2_client):
    client, _ = phase2_client
    food = create_food(client, "  Brown   Rice  ")
    assert food["normalized_name"] == "brown rice"
    assert client.get(f"/v1/foods/{food['id']}").status_code == 200
    assert len(client.get("/v1/foods/search", params={"q": "BROWN RICE"}).json()["foods"]) == 1
    updated = food_payload("White Rice")
    response = client.put(f"/v1/foods/{food['id']}", json=updated)
    assert response.status_code == 200
    assert response.json()["normalized_name"] == "white rice"
    assert client.delete(f"/v1/foods/{food['id']}").status_code == 204
    assert client.get(f"/v1/foods/{food['id']}").status_code == 404


def test_recipe_crud_isolation_and_cascade(phase2_client):
    client, session_factory = phase2_client
    food = create_food(client)
    first = create_household(client)
    second = create_household(client)
    response = client.post(
        f"/v1/households/{first}/recipes", headers={"Idempotency-Key": str(uuid4())}, json=recipe_payload([food["id"]])
    )
    assert response.status_code == 201, response.text
    recipe_id = response.json()["id"]
    assert len(client.get(f"/v1/households/{first}/recipes").json()) == 1
    assert client.get(f"/v1/households/{second}/recipes").json() == []
    assert client.get(f"/v1/households/{second}/recipes/{recipe_id}").status_code == 404
    with session_factory() as session:
        assert session.scalar(
            select(RecipeIngredient.recipe_id).where(RecipeIngredient.recipe_id == UUID(recipe_id))
        )
    assert client.delete(f"/v1/households/{first}/recipes/{recipe_id}?expected_version=1").status_code == 204
    with session_factory() as session:
        assert session.scalar(
            select(RecipeIngredient.recipe_id).where(RecipeIngredient.recipe_id == UUID(recipe_id))
        ) is None
        assert session.scalar(
            select(RecipeInstruction.recipe_id).where(RecipeInstruction.recipe_id == UUID(recipe_id))
        ) is None


def test_recipe_nutrition_scales_and_propagates_metadata(phase2_client):
    client, _ = phase2_client
    rice = create_food(client, "Rice")
    chicken = create_food(
        client,
        "Chicken",
        calories_per_serving=165,
        protein_g=31,
        carbohydrate_g=0,
        fat_g=4,
        allergens=[{"allergen": "chicken", "relationship_type": "contains"}],
        dietary_tags=[],
    )
    household = create_household(client)
    recipe = client.post(
        f"/v1/households/{household}/recipes", headers={"Idempotency-Key": str(uuid4())},
        json=recipe_payload([rice["id"], chicken["id"]], servings=2),
    )
    assert recipe.status_code == 201, recipe.text
    nutrition = client.get(
        f"/v1/households/{household}/recipes/{recipe.json()['id']}/nutrition"
    )
    assert nutrition.status_code == 200
    result = nutrition.json()
    assert Decimal(str(result["total_calories"])) == Decimal(295)
    assert Decimal(str(result["calories_per_serving"])) == Decimal("147.5")
    assert result["aggregated_allergens"] == ["chicken"]
    assert result["dietary_tags"] == []
    assert result["calculation_version"] == "recipe-nutrition-v1"


def test_conversion_support_and_structured_unsupported_error(phase2_client):
    client, _ = phase2_client
    food = create_food(client, "Liquid test", serving_unit="ml", serving_quantity=100)
    household = create_household(client)
    recipe = client.post(
        f"/v1/households/{household}/recipes", headers={"Idempotency-Key": str(uuid4())},
        json=recipe_payload([food["id"]], units=["pinch"]),
    )
    assert recipe.status_code == 201
    response = client.get(
        f"/v1/households/{household}/recipes/{recipe.json()['id']}/nutrition"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_conversion"


def test_unknown_density_warns_and_incomplete_food_warns(phase2_client):
    client, _ = phase2_client
    food = create_food(client, "Unknown density", serving_unit="g")
    household = create_household(client)
    recipe = client.post(
        f"/v1/households/{household}/recipes", headers={"Idempotency-Key": str(uuid4())},
        json=recipe_payload([food["id"]], units=["cup"]),
    )
    response = client.get(
        f"/v1/households/{household}/recipes/{recipe.json()['id']}/nutrition"
    )
    assert response.status_code == 200
    assert "Missing gram conversion" in response.json()["warnings"][0]

    incomplete = create_food(
        client,
        "Incomplete",
        calories_per_serving=None,
        protein_g=None,
        carbohydrate_g=None,
        fat_g=None,
    )
    recipe = client.post(
        f"/v1/households/{household}/recipes", headers={"Idempotency-Key": str(uuid4())}, json=recipe_payload([incomplete["id"]])
    )
    response = client.get(
        f"/v1/households/{household}/recipes/{recipe.json()['id']}/nutrition"
    )
    assert "incomplete nutrition data" in response.json()["warnings"][0]


def test_system_recipe_is_readable_but_not_editable(phase2_client):
    client, session_factory = phase2_client
    food = create_food(client)
    household = create_household(client)
    with session_factory() as session:
        system_recipe = Recipe(name="System fixture", servings=Decimal(1), household_id=None)
        system_recipe.ingredients = [
            RecipeIngredient(food_id=UUID(food["id"]), quantity=Decimal(100), unit="g", display_order=0)
        ]
        session.add(system_recipe)
        session.commit()
        system_id = str(system_recipe.id)
    assert client.get(f"/v1/households/{household}/recipes/{system_id}").status_code == 200
    response = client.put(
        f"/v1/households/{household}/recipes/{system_id}",
        json={**recipe_payload([food["id"]]), "expected_version": 1},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"