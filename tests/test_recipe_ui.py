import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from nourish_nest.api_client import APIClient, APIResponseError, DashboardCounts, Health, Household
from nourish_nest.recipe_client_models import RecipeInput, RecipeNutrition, RecipeRecord, StoredFood
from nourish_nest.recipe_ui import (
    add_ingredient,
    build_payload,
    finish_save,
    move_row,
    new_draft,
    nutrition_rows,
)

HOME = Household(id=UUID(int=1), name="Recipe Home")
FOOD = StoredFood(id=UUID(int=2), name="Rice", brand="Test brand")
PAYLOAD = {
    "name": "Rice bowl",
    "description": "Simple bowl",
    "cuisine": "Indian",
    "preparation_minutes": 5,
    "cooking_minutes": 20,
    "servings": "2.5",
    "source": "Family",
    "ingredients": [
        {"food_id": str(FOOD.id), "quantity": "100.125", "unit": "g", "display_order": 0}
    ],
    "instructions": [{"step_number": 1, "instruction": "Cook rice."}],
}


RECIPE_ID = UUID(int=3)


def record(payload=None, *, recipe_id=RECIPE_ID, household_id=HOME.id):
    values = (
        payload.model_dump(mode="json")
        if isinstance(payload, RecipeInput)
        else dict(payload or PAYLOAD)
    )
    values["ingredients"] = [
        {**i, "food": FOOD.model_dump(mode="json")} for i in values["ingredients"]
    ]
    return RecipeRecord(**values, id=recipe_id, household_id=household_id)


OWN = record()
SYSTEM = record(
    {**PAYLOAD, "name": "Shared bowl", "cuisine": "Thai"}, recipe_id=UUID(int=4), household_id=None
)
MACROS = {
    "protein_g": "10",
    "carbohydrate_g": "20",
    "fat_g": "5",
    "fiber_g": None,
    "sugar_g": "0",
    "sodium_mg": "25",
}
NUTRITION = RecipeNutrition(
    total_calories="123.45",
    total_macros=MACROS,
    calories_per_serving="49.38",
    macros_per_serving=MACROS,
    aggregated_allergens=["soy"],
    dietary_tags=["vegetarian"],
    warnings=["Missing data for one ingredient"],
    calculation_version="recipe-nutrition-v1",
)


@pytest.fixture
def api(monkeypatch):
    mock = MagicMock(spec=APIClient)
    mock.__enter__.return_value = mock
    mock.health.return_value = Health(status="ok", version="test")
    mock.households.return_value = [HOME]
    mock.recipes.return_value = [OWN, SYSTEM]
    mock.get_recipe.side_effect = lambda home, rid: next(
        r for r in mock.recipes.return_value if r.id == rid
    )
    mock.recipe_nutrition.return_value = NUTRITION
    mock.dashboard.return_value = DashboardCounts(
        members=0,
        recipes=2,
        active_pantry_items=0,
        expiring_items=0,
        low_stock_items=0,
        active_grocery_lists=0,
    )
    mock.search_foods.return_value = [FOOD]
    mock.create_recipe.side_effect = lambda home, payload: record(payload, recipe_id=UUID(int=5))
    mock.update_recipe.side_effect = lambda home, rid, payload: record(payload, recipe_id=rid)
    monkeypatch.setattr("nourish_nest.streamlit_ui.create_api_client", lambda: mock)
    return mock


def app():
    ui = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=20
    )
    ui.session_state["page"] = "Recipes"
    return ui.run()


def button(ui, label):
    return next(b for b in ui.button if b.label == label)


def draft(ui):
    return ui.session_state[f"recipes_{HOME.id}"]["draft"]


def test_browse_filter_and_system_read_only(api):
    ui = app()
    assert not ui.exception
    assert any(c.value == "Household recipe" for c in ui.caption)
    assert any("100.125 g" in m.value for m in ui.markdown)
    assert any("recipe-nutrition-v1" in c.value for c in ui.caption)
    assert any("Missing data" in w.value for w in ui.warning)
    assert any("soy" in m.value for m in ui.markdown)
    ui.selectbox(key=f"recipe_cuisine_{HOME.id}").select("Thai").run()
    assert not ui.exception
    assert any("read-only" in i.value for i in ui.info)
    assert not any(b.label in ("Edit recipe", "Delete recipe") for b in ui.button)
    ui.text_input(key=f"recipe_search_{HOME.id}").input("no match").run()
    assert any("No recipes match" in i.value for i in ui.info)
    api.update_recipe.assert_not_called()
    api.delete_recipe.assert_not_called()


def test_navigation_and_household_drafts_are_isolated(api):
    other = Household(id=UUID(int=9), name="Second home")
    api.households.return_value = [HOME, other]
    ui = app()
    ui.radio(key="page").set_value("Dashboard").run()
    button(ui, "Browse recipes").click().run()
    assert ui.session_state["page"] == "Recipes"
    assert ui.session_state["household_id"] == str(HOME.id)
    button(ui, "Create recipe").click().run()
    token = draft(ui)["token"]
    ui.text_input(key=f"{token}_name").input("Private draft").run()
    ui.selectbox(key="household_id").select(str(other.id)).run()
    assert not ui.exception
    assert ui.session_state[f"recipes_{other.id}"]["draft"] is None
    assert all(r.household_id is None for r in ui.session_state[f"recipes_{other.id}"]["recipes"])
    ui.selectbox(key="household_id").select(str(HOME.id)).run()
    assert draft(ui)["values"]["name"] == "Private draft"


def test_create_payload_and_reset_without_duplicate_submit(api):
    api.recipes.return_value = []
    ui = app()
    assert any("No recipes yet" in i.value for i in ui.info)
    button(ui, "Create recipe").click().run()
    token = draft(ui)["token"]
    ui.text_input(key=f"{token}_name").input("New bowl").run()
    button(ui, "Search foods").click().run()
    button(ui, "Add ingredient").click().run()
    first = draft(ui)["ingredients"][0]
    ui.text_input(key=f"{token}_{first['row_id']}_quantity").input("1.125").run()
    button(ui, "Add ingredient").click().run()
    button(ui, "Add instruction").click().run()
    step = draft(ui)["instructions"][0]
    ui.text_area(key=f"{token}_{step['row_id']}_instruction").input("Cook.").run()
    button(ui, "Save recipe").click().run()
    assert not ui.exception
    payload = api.create_recipe.call_args.args[1]
    assert payload.name == "New bowl"
    assert payload.ingredients[0].quantity == Decimal("1.125")
    assert [i.display_order for i in payload.ingredients] == [0, 1]
    assert payload.instructions[0].step_number == 1
    assert draft(ui) is None
    assert any("Saved New bowl" in s.value for s in ui.success)
    ui.run()
    api.create_recipe.assert_called_once()
    button(ui, "Create recipe").click().run()
    assert draft(ui)["token"] != token
    assert draft(ui)["ingredients"] == []
    assert draft(ui)["values"]["name"] == ""


def test_edit_and_delete(api):
    ui = app()
    button(ui, "Edit recipe").click().run()
    token = draft(ui)["token"]
    assert ui.text_input(key=f"{token}_name").value == "Rice bowl"
    ui.text_input(key=f"{token}_name").input("Updated bowl").run()
    button(ui, "Save recipe").click().run()
    assert not ui.exception
    assert api.update_recipe.call_args.args[:2] == (HOME.id, OWN.id)
    assert api.update_recipe.call_args.args[2].name == "Updated bowl"
    assert button(ui, "Delete recipe").disabled
    ui.checkbox[0].check().run()
    button(ui, "Delete recipe").click().run()
    assert not ui.exception
    api.delete_recipe.assert_called_once_with(HOME.id, OWN.id)
    assert any("Deleted Updated bowl" in s.value for s in ui.success)


def test_editor_reordering_removal_and_validation(api):
    ui = app()
    button(ui, "Edit recipe").click().run()
    token = draft(ui)["token"]
    button(ui, "Search foods").click().run()
    button(ui, "Add ingredient").click().run()
    rows = draft(ui)["ingredients"]
    second_id = rows[1]["row_id"]
    ui.button(key=f"{token}_{second_id}_up").click().run()
    assert draft(ui)["ingredients"][0]["row_id"] == second_id
    ui.button(key=f"{token}_{second_id}_remove").click().run()
    assert len(draft(ui)["ingredients"]) == 1
    ui.text_input(key=f"{token}_servings").input("0").run()
    button(ui, "Save recipe").click().run()
    assert ui.error and not ui.exception
    api.update_recipe.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "delete", "nutrition"])
def test_structured_errors(api, operation):
    error = APIResponseError("conflict", "Recipe unavailable", "recipe-trace", 409)
    if operation == "nutrition":
        api.recipe_nutrition.side_effect = error
    ui = app()
    if operation == "create":
        button(ui, "Edit recipe").click().run()
        api.update_recipe.side_effect = error
        button(ui, "Save recipe").click().run()
        assert draft(ui) is not None
    elif operation == "delete":
        api.delete_recipe.side_effect = error
        ui.checkbox[0].check().run()
        button(ui, "Delete recipe").click().run()
    assert not ui.exception
    assert any(t.value == "Request ID: recipe-trace" for t in ui.text)
    assert any(e.value == "Recipe unavailable" for e in ui.error)


@pytest.mark.parametrize(
    "change",
    [
        {"servings": "0"},
        {"servings": "-1"},
        {"name": " "},
        {"ingredients": []},
        {
            "ingredients": [
                {"food_id": str(FOOD.id), "quantity": "0", "unit": "g", "display_order": 0}
            ]
        },
        {
            "ingredients": [
                {"food_id": str(FOOD.id), "quantity": "0.0001", "unit": "g", "display_order": 0}
            ]
        },
        {
            "instructions": [
                {"step_number": 1, "instruction": "Cook"},
                {"step_number": 1, "instruction": "Serve"},
            ]
        },
    ],
)
def test_payload_validation(change):
    with pytest.raises(ValidationError):
        RecipeInput(**{**PAYLOAD, **change})


def test_draft_helpers_and_nutrition_rendering():
    data = new_draft(OWN)
    add_ingredient(data, FOOD)
    move_row(data["ingredients"], 1, -1)
    data["instructions"].append({"instruction": "Serve", "row_id": "new"})
    move_row(data["instructions"], 1, -1)
    payload = build_payload(data)
    assert payload.instructions[0].instruction == "Serve"
    assert [s.step_number for s in payload.instructions] == [1, 2]
    workspace = {"recipes": [OWN], "draft": data}
    finish_save(workspace, OWN)
    assert workspace["draft"] is None and workspace["selected"] == str(OWN.id)
    rows = nutrition_rows(NUTRITION)
    assert rows[0]["Per serving"] == "49.38 kcal"
    assert rows[4]["Recipe total"] == "Not available"


def test_http_recipe_contracts():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1/foods/search":
            assert request.url.params["q"] == "rice & beans"
            return httpx.Response(200, json={"foods": [FOOD.model_dump(mode="json")]})
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/nutrition"):
            return httpx.Response(200, json=NUTRITION.model_dump(mode="json"))
        if request.method == "GET" and request.url.path.endswith("/recipes"):
            return httpx.Response(200, json=[OWN.model_dump(mode="json")])
        if request.method in ("POST", "PUT"):
            assert json.loads(request.content)["ingredients"][0]["quantity"] == "100.125"
        return httpx.Response(
            201 if request.method == "POST" else 200, json=OWN.model_dump(mode="json")
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        api = APIClient(client=transport)
        assert api.search_foods("rice & beans") == [FOOD]
        assert api.recipes(HOME.id) == [OWN]
        assert api.get_recipe(HOME.id, OWN.id) == OWN
        api.create_recipe(HOME.id, RecipeInput(**PAYLOAD))
        api.update_recipe(HOME.id, OWN.id, RecipeInput(**PAYLOAD))
        assert api.recipe_nutrition(HOME.id, OWN.id) == NUTRITION
        api.delete_recipe(HOME.id, OWN.id)
    base = f"/v1/households/{HOME.id}/recipes"
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", "/v1/foods/search"),
        ("GET", base),
        ("GET", f"{base}/{OWN.id}"),
        ("POST", base),
        ("PUT", f"{base}/{OWN.id}"),
        ("GET", f"{base}/{OWN.id}/nutrition"),
        ("DELETE", f"{base}/{OWN.id}"),
    ]
