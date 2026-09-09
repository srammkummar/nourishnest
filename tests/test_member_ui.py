from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from nourish_nest.api_client import (
    APIClient,
    APIResponseError,
    DashboardCounts,
    Health,
    Household,
    Member,
    MemberInput,
    MemberNutrition,
)

HOME = Household(id=UUID(int=1), name="Maple House")
OTHER = Household(id=UUID(int=2), name="Other House")
PROFILE = {
    "name": "Alex",
    "age": 35,
    "sex": "female",
    "height_cm": 165.0,
    "weight_kg": 68.0,
    "activity_level": "moderate",
    "goal": "maintain",
    "weekly_goal_kg": 0.0,
    "meals_per_day": 3,
    "dietary_preferences": [{"preference_type": "custom", "value": "Low sodium"}],
    "allergies": [{"allergen": "Peanuts", "severity": "severe", "notes": "Carry medication"}],
}
PERSON = Member(id=UUID(int=3), household_id=HOME.id, **PROFILE)
PLAN = MemberNutrition(
    bmr_calories=1400,
    maintenance_calories=2100,
    target_calories=2100,
    macros={"protein_g": 100, "carbohydrate_g": 250, "fat_g": 60},
    calculation_version="mifflin-st-jeor-v1",
    warnings=["Example API warning"],
)


@pytest.fixture
def api(monkeypatch):
    mock = MagicMock(spec=APIClient)
    mock.__enter__.return_value = mock
    mock.health.return_value = Health(status="ok", version="test")
    mock.households.return_value = [HOME, OTHER]
    mock.members.return_value = [PERSON]
    mock.dashboard.return_value = DashboardCounts(
        members=1,
        recipes=0,
        active_pantry_items=0,
        expiring_items=0,
        low_stock_items=0,
        active_grocery_lists=0,
    )
    mock.member_nutrition.return_value = PLAN
    mock.create_member.side_effect = lambda household_id, payload: Member(
        id=UUID(int=4), household_id=household_id, **payload.model_dump()
    )
    mock.update_member.side_effect = lambda member_id, payload: Member(
        id=member_id, household_id=HOME.id, **payload.model_dump()
    )
    monkeypatch.setattr("nourish_nest.streamlit_ui.create_api_client", lambda: mock)
    return mock


def app(page="Household"):
    ui = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=15
    )
    ui.session_state["page"] = page
    return ui.run()


def button(ui, label):
    return next(b for b in ui.button if b.label == label)


def test_no_household(api):
    api.households.return_value = []
    ui = app("Nutrition")
    assert not ui.exception
    assert any(h.value == "Welcome home" for h in ui.subheader)
    api.members.assert_not_called()
    api.member_nutrition.assert_not_called()


def test_member_listing(api):
    ui = app()
    assert not ui.exception
    assert any(h.value == "Alex" for h in ui.subheader)
    assert any(str(HOME.id) in t.value for t in ui.text)
    assert any("Peanuts (severe)" in t.value for t in ui.markdown)
    assert any("Low sodium" in t.value for t in ui.markdown)


def test_create_member(api):
    api.members.return_value = []
    ui = app()
    ui.text_input(key=f"add_{HOME.id}_name").input("Sam")
    button(ui, "Add member").click().run()
    assert not ui.exception
    payload = api.create_member.call_args.args[1]
    assert payload.name == "Sam"
    assert payload.weekly_goal_kg == 0
    assert any(h.value == "Sam" for h in ui.subheader)
    assert any("Saved Sam" in s.value for s in ui.success)
    api.dashboard.assert_not_called()


def test_edit_preserves_preferences_and_allergies(api):
    ui = app()
    ui.radio(key=f"member_action_{HOME.id}").set_value("Edit member").run()
    ui.text_input(key=f"edit_{PERSON.id}_name").input("Alex updated")
    button(ui, "Save member").click().run()
    assert not ui.exception
    member_id, payload = api.update_member.call_args.args
    assert member_id == PERSON.id
    assert payload.name == "Alex updated"
    assert payload.dietary_preferences == PERSON.dietary_preferences
    assert payload.allergies == PERSON.allergies
    assert any(h.value == "Alex updated" for h in ui.subheader)


def test_create_with_preference_and_allergy_rows(api):
    ui = app()
    ui.text_input(key=f"add_{HOME.id}_name").input("Sam")
    ui.session_state[f"add_{HOME.id}_preferences"] = {
        "edited_rows": {},
        "added_rows": [{"preference_type": "vegan", "value": "Vegan"}],
        "deleted_rows": [],
    }
    ui.session_state[f"add_{HOME.id}_allergies"] = {
        "edited_rows": {},
        "added_rows": [{"allergen": "Milk", "severity": "mild", "notes": None}],
        "deleted_rows": [],
    }
    button(ui, "Add member").click().run()
    assert not ui.exception
    payload = api.create_member.call_args.args[1]
    assert payload.dietary_preferences[0].value == "Vegan"
    assert payload.allergies[0].allergen == "Milk"
    assert payload.allergies[0].notes is None


def test_edit_can_remove_preference_and_allergy_rows(api):
    ui = app()
    ui.radio(key=f"member_action_{HOME.id}").set_value("Edit member").run()
    for suffix in ("preferences", "allergies"):
        ui.session_state[f"edit_{PERSON.id}_{suffix}"] = {
            "edited_rows": {},
            "added_rows": [],
            "deleted_rows": [0],
        }
    button(ui, "Save member").click().run()
    assert not ui.exception
    payload = api.update_member.call_args.args[1]
    assert payload.dietary_preferences == []
    assert payload.allergies == []


def test_delete_requires_confirmation(api):
    ui = app()
    ui.radio(key=f"member_action_{HOME.id}").set_value("Delete member").run()
    assert button(ui, "Delete member").disabled
    api.delete_member.assert_not_called()
    ui.checkbox[0].check().run()
    button(ui, "Delete member").click().run()
    assert not ui.exception
    api.delete_member.assert_called_once_with(PERSON.id)
    assert any("Deleted Alex" in s.value for s in ui.success)
    assert not any(h.value == "Alex" for h in ui.subheader)


@pytest.mark.parametrize("field,value", [("name", "   "), ("height", 100.0), ("weight", 30.0)])
def test_validation_before_submission(api, field, value):
    ui = app()
    ui.text_input(key=f"add_{HOME.id}_name").input("Sam")
    if field == "name":
        ui.text_input(key=f"add_{HOME.id}_name").input(value)
    else:
        ui.number_input(key=f"add_{HOME.id}_{field}").set_value(value)
    button(ui, "Add member").click().run()
    assert not ui.exception
    assert ui.error
    api.create_member.assert_not_called()


def test_mutation_error_request_id(api):
    api.create_member.side_effect = APIResponseError(
        "validation_error", "Invalid member", "member-trace", 422
    )
    ui = app()
    ui.text_input(key=f"add_{HOME.id}_name").input("Sam")
    button(ui, "Add member").click().run()
    assert not ui.exception
    assert any(e.value == "Invalid member" for e in ui.error)
    assert any("member-trace" in t.value for t in ui.text)
    assert not any("Saved Sam" in s.value for s in ui.success)
    api.create_member.assert_called_once()


def test_nutrition_display(api):
    ui = app("Nutrition")
    api.member_nutrition.assert_not_called()
    button(ui, "Calculate nutrition").click().run()
    assert not ui.exception
    assert [m.value for m in ui.metric] == [
        "1,400 kcal/day",
        "2,100 kcal/day",
        "2,100 kcal/day",
        "100 g/day",
        "250 g/day",
        "60 g/day",
    ]
    assert any("mifflin-st-jeor-v1" in c.value for c in ui.caption)
    assert any("not medical advice" in c.value for c in ui.caption)
    assert ui.warning[0].value == "Example API warning"
    api.member_nutrition.assert_called_once_with(PERSON.id)
    api.update_member.assert_not_called()


def test_missing_member_and_empty_nutrition(api):
    api.members.return_value = []
    ui = app("Nutrition")
    assert any("No saved members" in i.value for i in ui.info)
    api.member_nutrition.assert_not_called()
    api.members.return_value = [PERSON]
    ui.run()
    api.member_nutrition.side_effect = APIResponseError(
        "not_found", "Member not found", "missing-trace", 404
    )
    button(ui, "Calculate nutrition").click().run()
    assert not ui.exception
    assert any("missing-trace" in t.value for t in ui.text)
    assert not ui.metric


def test_navigation_and_household_selection(api):
    ui = app("Dashboard")
    button(ui, "Add member").click().run()
    assert ui.session_state["page"] == "Household"
    ui.radio(key="page").set_value("Dashboard").run()
    button(ui, "Calculate nutrition").click().run()
    assert ui.session_state["page"] == "Nutrition"
    assert ui.session_state["household_id"] == str(HOME.id)
    ui.selectbox(key="household_id").select(str(OTHER.id)).run()
    assert not ui.exception
    assert any("No saved members" in i.value for i in ui.info)
    ui.run()
    assert ui.session_state["household_id"] == str(OTHER.id)
    api.member_nutrition.assert_not_called()


@pytest.mark.parametrize(
    "patch",
    [
        {"goal": "lose", "weekly_goal_kg": 0},
        {"age": 12},
        {"allergies": [{"allergen": "", "severity": "severe"}]},
        {"dietary_preferences": [{"preference_type": "invalid", "value": "x"}]},
    ],
)
def test_member_input_validation(patch):
    with pytest.raises(ValidationError):
        MemberInput(**{**deepcopy(PROFILE), **patch})


def test_http_member_contracts():
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/nutrition/calculate"):
            return httpx.Response(200, json=PLAN.model_dump(mode="json"))
        if request.method == "GET":
            return httpx.Response(200, json=[PERSON.model_dump(mode="json")])
        return httpx.Response(
            201 if request.method == "POST" else 200, json=PERSON.model_dump(mode="json")
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        api = APIClient(client=transport)
        assert api.members(HOME.id)[0] == PERSON
        api.create_member(HOME.id, MemberInput(**PROFILE))
        api.update_member(PERSON.id, MemberInput(**PROFILE))
        assert api.delete_member(PERSON.id) is None
        assert api.member_nutrition(PERSON.id) == PLAN
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", f"/v1/households/{HOME.id}/members"),
        ("POST", f"/v1/households/{HOME.id}/members"),
        ("PUT", f"/v1/members/{PERSON.id}"),
        ("DELETE", f"/v1/members/{PERSON.id}"),
        ("POST", f"/v1/members/{PERSON.id}/nutrition/calculate"),
    ]


@pytest.mark.parametrize("operation", ["update_member", "delete_member", "member_nutrition"])
def test_member_mutations_never_retry(operation):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            503,
            json={"code": "unavailable", "message": "Try later", "request_id": "mutation-trace"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        api = APIClient(client=transport)
        with pytest.raises(APIResponseError):
            getattr(api, operation)(
                PERSON.id, *([MemberInput(**PROFILE)] if operation == "update_member" else [])
            )
    assert len(calls) == 1
