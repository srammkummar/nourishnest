from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from test_grocery_ui import GENERATION, HOME, LIST, OWN, api, app, button  # noqa: F401
from test_member_ui import PERSON, PLAN

from nourish_nest.api_client import APIClient, APIResponseError
from nourish_nest.planning_contracts import (
    RecipeRecommendation,
    RecommendationRequest,
    RecommendationResponse,
)
from nourish_nest.planning_ui import consolidate, new_plan, nutrition_summary

RECIPE = RecipeRecommendation(
    recipe_id=OWN.id,
    recipe_name="Rice bowl",
    system_recipe=True,
    servings="2",
    cuisine="Italian",
    preparation_minutes=5,
    cooking_minutes=15,
    coverage_percentage="50",
    classification="Missing 1–2 ingredients",
    missing_ingredient_count=1,
    requirements=[],
    missing_ingredients=[],
    expiring_ingredients=[],
    score={"coverage_points": "40", "expiring_points": "0", "total": "40"},
    explanation="Pantry covers half the required quantity.",
    nutrition_per_serving={
        "calories": "123.125",
        "protein_g": "1.25",
        "carbohydrate_g": "2.5",
        "fat_g": "3.75",
    },
    warnings=[{"code": "may_contain", "message": "May contain milk."}],
)
RESULT = RecommendationResponse(
    household_id=HOME.id,
    member_id=None,
    recommendations=[RECIPE],
    warnings=[],
    calculation_as_of=datetime(2026, 9, 10, tzinfo=UTC),
)


@pytest.fixture
def planner_api(request):
    mock = request.getfixturevalue("api")
    mock.members.return_value = [PERSON.model_copy(update={"household_id": HOME.id})]
    mock.member_nutrition.return_value = PLAN
    mock.recipe_recommendations.return_value = RESULT
    return mock


def open_planner():
    ui = app()
    ui.radio(key="page").set_value("Meal Planner").run()
    assert not ui.exception
    return ui


def workspace(ui, member=None):
    return ui.session_state[f"planner_{HOME.id}_{member}"]


def add_meal(ui):
    button(ui, "Find recipes").click().run()
    button(ui, "Add to weekly plan").click().run()
    assert not ui.exception


def test_recommendation_to_plan_and_grocery_preview(planner_api):
    ui = open_planner()
    add_meal(ui)
    assert workspace(ui)["meals"][("Monday", "Breakfast")]["recipe"].recipe_id == OWN.id
    assert any("May contain milk" in value.value for value in ui.warning)
    assert any("Rice bowl" in value.value for value in ui.subheader)
    button(ui, "Prepare grocery needs").click().run()
    assert not ui.exception
    for method in (planner_api.grocery_requirements, planner_api.grocery_shortages):
        household, request = method.call_args.args
        assert household == HOME.id
        assert request.recipes[0].recipe_id == OWN.id
        assert request.recipes[0].desired_servings == 1
    assert all(
        str(OWN.id) not in option for selector in ui.selectbox for option in selector.options
    )
    assert all(
        not expander.proto.expanded
        for expander in ui.expander
        if expander.label == "Technical details"
    )


def test_session_navigation_and_member_isolation(planner_api):
    ui = open_planner()
    add_meal(ui)
    ui.radio(key="page").set_value("Dashboard").run()
    ui.radio(key="page").set_value("Meal Planner").run()
    assert len(workspace(ui)["meals"]) == 1
    ui.selectbox(key=f"planner_member_{HOME.id}").select(PERSON.id).run()
    assert not workspace(ui, PERSON.id)["meals"]
    assert len(workspace(ui)["meals"]) == 1
    assert not new_plan()["meals"]


def test_nutrition_scaling_and_unknown_values():
    meals = {
        ("Monday", "Breakfast"): {"recipe": RECIPE, "servings": Decimal("0.1")},
        ("Tuesday", "Dinner"): {"recipe": RECIPE, "servings": Decimal("0.2")},
    }
    days, week, incomplete = nutrition_summary(meals)
    assert week["calories"] == Decimal("36.9375")
    assert days["Monday"]["protein_g"] == Decimal("0.125") and not incomplete
    request = consolidate(meals)
    assert len(request.recipes) == 1 and request.recipes[0].desired_servings == Decimal("0.3")
    unknown = RECIPE.model_copy(deep=True)
    unknown.nutrition_per_serving.calories = None
    meals[("Wednesday", "Lunch")] = {"recipe": unknown, "servings": Decimal(1)}
    _, week, incomplete = nutrition_summary(meals)
    assert week["calories"] is None and "Wednesday" in incomplete


@pytest.mark.parametrize("age", [17, 35])
def test_member_targets_do_not_apply_to_minors(planner_api, age):
    planner_api.members.return_value = [
        PERSON.model_copy(update={"age": age, "household_id": HOME.id})
    ]
    ui = open_planner()
    ui.selectbox(key=f"planner_member_{HOME.id}").select(PERSON.id).run()
    add_meal(ui)
    assert planner_api.recipe_recommendations.call_args.args[-1].member_id == PERSON.id
    if age < 18:
        assert not any(b.label == "Compare with member target" for b in ui.button)
        assert any("under 18" in value.value for value in ui.info)
        planner_api.member_nutrition.assert_not_called()
    else:
        button(ui, "Compare with member target").click().run()
        planner_api.member_nutrition.assert_called_once_with(HOME.id, PERSON.id)
        assert not ui.exception
        assert any("Week minus target" in str(frame.value) for frame in ui.dataframe)


def test_generation_retry_retains_exact_request(planner_api):
    ui = open_planner()
    add_meal(ui)
    button(ui, "Prepare grocery needs").click().run()
    planner_api.generate_grocery_list.side_effect = APIResponseError(
        "api_timeout", "Timed out", "plan-trace"
    )
    button(ui, "Generate grocery list").click().run()
    pending = workspace(ui)["pending"]["data"].model_copy(deep=True)
    assert pending.expected_list_version == LIST.version
    assert any("plan-trace" in value.value for value in ui.text)
    planner_api.generate_grocery_list.side_effect = None
    button(ui, "Retry grocery generation").click().run()
    assert not ui.exception
    assert planner_api.generate_grocery_list.call_args.args[-1] == pending
    assert workspace(ui)["pending"] is None
    assert workspace(ui)["generation"] == GENERATION


def test_invalid_servings_keep_plan_and_fields(planner_api):
    ui = open_planner()
    button(ui, "Find recipes").click().run()
    field = next(f for f in ui.text_input if f.label == "Servings to plan")
    field.input("0")
    button(ui, "Add to weekly plan").click().run()
    assert not workspace(ui)["meals"]
    assert next(f for f in ui.text_input if f.label == "Servings to plan").value == "0"
    assert ui.error and not ui.exception


def test_client_recommendation_contract():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=RESULT.model_dump(mode="json"))

    with APIClient(
        "http://test",
        client=httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler)),
    ) as client:
        response = client.recipe_recommendations(
            HOME.id, RecommendationRequest(member_id=UUID(int=99))
        )
    assert response == RESULT
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"/v1/households/{HOME.id}/recipe-recommendations"
    assert str(UUID(int=99)).encode() in requests[0].content
