import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest
from test_recipe_ui import OWN, SYSTEM

import nourish_nest.grocery_client_models as wire
from nourish_nest.api_client import APIClient, APIResponseError, DashboardCounts, Health, Household
from nourish_nest.grocery_ui import item_rows, requirement_rows
from nourish_nest.pantry_client_models import LocationRecord, PantryOverview
from nourish_nest.recipe_client_models import StoredFood

HOME = Household(id=UUID(int=1), name="Grocery home")
FOOD = StoredFood(id=UUID(int=2), name="Rice")
LIST = wire.GroceryList(id=UUID(int=3), household_id=HOME.id, name="Weekly", version=2)
ITEM = wire.GroceryItem(
    id=UUID(int=4),
    grocery_list_id=LIST.id,
    display_name="Rice",
    food_id=FOOD.id,
    required_quantity="2.125001",
    required_unit="g",
    version=3,
)
LOCATION = LocationRecord(
    id=UUID(int=5), household_id=HOME.id, name="Pantry", location_type="pantry"
)
SELECTION = wire.RequirementsInput(recipes=[{"recipe_id": OWN.id, "desired_servings": "4"}])
REQUIREMENTS = wire.RequirementsResult(
    household_id=HOME.id,
    recipes=SELECTION.recipes,
    requirements=[
        {
            "food_id": FOOD.id,
            "food_name": "Rice",
            "required_quantity": "2.125001",
            "canonical_unit": "g",
            "sources": [
                {
                    "recipe_id": OWN.id,
                    "recipe_name": OWN.name,
                    "ingredient_id": UUID(int=6),
                    "scaled_quantity": "2.125001",
                    "original_unit": "g",
                    "required_quantity": "2.125001",
                }
            ],
        }
    ],
    warnings=[
        {
            "code": "unsupported_conversion",
            "message": "Incomplete fixture warning",
            "food_id": FOOD.id,
        }
    ],
    calculation_version="grocery-requirements-v1",
)
SHORTAGE = wire.ShortageResult(
    **{
        **REQUIREMENTS.model_dump(),
        "requirements": [
            {
                **REQUIREMENTS.requirements[0].model_dump(),
                "available_quantity": "1",
                "shortage_quantity": "1.125001",
                "purchase_required": True,
                "pantry_lots": [
                    {
                        "pantry_item_id": UUID(int=7),
                        "quantity": "1",
                        "original_unit": "g",
                        "available_quantity": "1",
                        "expiration_date": None,
                    }
                ],
            }
        ],
        "calculation_as_of": datetime(2026, 9, 9, tzinfo=UTC),
        "calculation_version": "grocery-shortage-v1",
    }
)
GENERATION = wire.GenerationResult(
    generation_run_id=UUID(int=8),
    grocery_list_id=LIST.id,
    grocery_list_version=3,
    replayed=False,
    created_items=[
        {
            **ITEM.model_dump(),
            "source_type": "recipe",
            "generation_run_id": UUID(int=8),
            "recipe_sources": [
                {
                    "id": UUID(int=9),
                    "recipe_id": OWN.id,
                    "recipe_ingredient_id": UUID(int=6),
                    "required_quantity": "2.125001",
                    "canonical_unit": "g",
                }
            ],
        }
    ],
    warnings=REQUIREMENTS.warnings,
    warnings_available=True,
    calculation_as_of=datetime(2026, 9, 9, tzinfo=UTC),
    calculation_version="grocery-generation-v1",
)
PURCHASE = wire.PurchaseResult(
    purchase_event_id=UUID(int=10),
    grocery_list_id=LIST.id,
    grocery_list_item_id=ITEM.id,
    purchased_quantity="0.125001",
    purchased_unit="g",
    item_quantity="0.125001",
    item_unit="g",
    purchased_total="0.125001",
    checked=False,
    item_version=4,
    grocery_list_version=3,
    grocery_list_status="draft",
    add_to_pantry=True,
    allow_overpurchase=False,
    pantry_location_id=LOCATION.id,
    pantry_item_id=UUID(int=11),
    pantry_transaction_id=UUID(int=12),
    expiration_date=None,
    purchase_price="1.25",
    currency="USD",
    created_at=datetime(2026, 9, 9, tzinfo=UTC),
    replayed=False,
)


@pytest.fixture
def api(monkeypatch):
    mock = MagicMock(spec=APIClient)
    mock.__enter__.return_value = mock
    mock.health.return_value = Health(status="ok", version="test")
    mock.households.return_value = [HOME]
    mock.grocery_lists.return_value = [LIST]
    mock.grocery_list.return_value = LIST
    mock.grocery_items.return_value = [ITEM]
    mock.grocery_item.return_value = ITEM
    mock.create_grocery_list.return_value = LIST
    mock.recipes.return_value = [OWN, SYSTEM]
    mock.stored_food.return_value = FOOD
    mock.search_foods.return_value = [FOOD]
    mock.pantry_locations.return_value = [LOCATION]
    mock.grocery_requirements.return_value = REQUIREMENTS
    mock.grocery_shortages.return_value = SHORTAGE
    mock.generate_grocery_list.return_value = GENERATION
    mock.purchase_grocery_item.return_value = PURCHASE
    mock.pantry_overview.return_value = PantryOverview(
        active_items=1, expired_items=0, depleted_items=0, discarded_items=0, low_stock_food_ids=[]
    )
    mock.dashboard.return_value = DashboardCounts(
        members=0,
        recipes=2,
        active_pantry_items=1,
        expiring_items=0,
        low_stock_items=0,
        active_grocery_lists=1,
    )
    monkeypatch.setattr("nourish_nest.streamlit_ui.create_api_client", lambda: mock)
    return mock


def app():
    ui = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=20
    )
    ui.session_state["page"] = "Grocery Lists"
    return ui.run()


def button(ui, label):
    return next(b for b in ui.button if b.label == label)


def text(ui, label, value):
    next(i for i in ui.text_input if i.label == label).input(value)


def section(ui, value):
    ui.radio(key=f"groceries_{HOME.id}_section").set_value(value).run()


def workspace(ui):
    return ui.session_state[f"groceries_{HOME.id}"]


def record(ui):
    return workspace(ui)["records"][str(LIST.id)]


def select_recipe(ui):
    section(ui, "Recipe planning")
    ui.multiselect[0].set_value([OWN.id, SYSTEM.id]).run()


def test_list_crud_and_versions(api):
    ui = app()
    assert not ui.exception
    text(ui, "New list name", "New weekly")
    button(ui, "Create list").click().run()
    assert api.create_grocery_list.call_args.args[1].name == "New weekly"
    text(ui, "List name", "Updated weekly")
    button(ui, "Save list").click().run()
    assert api.update_grocery_list.call_args.args[-1].expected_version == 2
    assert button(ui, "Delete list").disabled
    ui.checkbox[0].check().run()
    button(ui, "Delete list").click().run()
    api.delete_grocery_list.assert_called_once_with(HOME.id, LIST.id, LIST.version)
    assert not ui.exception


def test_manual_item_crud_and_decimal(api):
    ui = app()
    section(ui, "Manual items")
    text(ui, "Item name", "Manual soap")
    text(ui, "Required quantity", "0.000001")
    button(ui, "Add item").click().run()
    data = api.create_grocery_item.call_args.args[-1]
    assert data.required_quantity == Decimal("0.000001") and data.food_id is None
    next(s for s in ui.selectbox if s.label == "Manual item").select(ITEM.id).run()
    text(ui, "Category (optional)", "Grains")
    button(ui, "Save item").click().run()
    assert api.update_grocery_item.call_args.args[-1].expected_version == 3
    next(s for s in ui.selectbox if s.label == "Manual item").select(ITEM.id).run()
    next(c for c in ui.checkbox if c.label == "Confirm deletion of this item").check().run()
    button(ui, "Delete item").click().run()
    api.delete_grocery_item.assert_called_once_with(HOME.id, LIST.id, ITEM.id, 3)
    assert not ui.exception


def test_stored_food_manual_item(api):
    ui = app()
    section(ui, "Manual items")
    ui.checkbox[0].check().run()
    button(ui, "Search foods").click().run()
    text(ui, "Item name", "Rice")
    button(ui, "Add item").click().run()
    assert api.create_grocery_item.call_args.args[-1].food_id == FOOD.id


def test_recipe_previews_and_household_system_selection(api):
    alien = OWN.model_copy(update={"id": UUID(int=99), "household_id": UUID(int=98)})
    api.recipes.return_value = [OWN, SYSTEM, alien]
    ui = app()
    select_recipe(ui)
    assert len(ui.multiselect[0].options) == 2
    text(ui, f"Desired servings: {OWN.name}", "4.125001")
    button(ui, "Preview recipe requirements").click().run()
    assert api.grocery_requirements.call_args.args[-1].recipes[0].desired_servings == Decimal(
        "4.125001"
    )
    assert any("Incomplete fixture warning" in w.value for w in ui.warning)
    button(ui, "Preview pantry shortages").click().run()
    assert any("point-in-time" in i.value for i in ui.info)
    assert requirement_rows(SHORTAGE)[0]["To purchase"] == "1.125001"
    section(ui, "Lists")
    section(ui, "Recipe planning")
    assert set(ui.multiselect[0].value) == {OWN.id, SYSTEM.id}
    assert not ui.exception


@pytest.mark.parametrize(
    "code",
    ["api_timeout", "idempotency_conflict", "grocery_generation_exists", "stale_grocery_version"],
)
def test_generation_retains_identical_request_and_errors(api, code):
    api.generate_grocery_list.side_effect = APIResponseError(
        code, "Generation failed", "generation-trace"
    )
    ui = app()
    select_recipe(ui)
    button(ui, "Generate into selected list").click().run()
    original = record(ui)["generation"]
    assert original.expected_list_version == 2
    assert any(t.value == "Request ID: generation-trace" for t in ui.text)
    assert any(c.value == f"Error code: {code}" for c in ui.text)
    api.grocery_list.return_value = LIST.model_copy(update={"version": 7})
    button(ui, "Refresh groceries").click().run()
    text(ui, f"Desired servings: {OWN.name}", "9")
    button(ui, "Retry generation").click().run()
    assert api.generate_grocery_list.call_args.args[-1] == original
    api.generate_grocery_list.side_effect = None
    button(ui, "Retry generation").click().run()
    assert record(ui)["generation"] is None
    assert record(ui)["generation_result"] == GENERATION
    assert any(s.value == "Saved generation" for s in ui.subheader)
    assert not ui.exception


def test_completed_list_cannot_generate(api):
    api.grocery_list.return_value = LIST.model_copy(update={"status": "completed"})
    ui = app()
    select_recipe(ui)
    assert button(ui, "Generate into selected list").disabled


def test_purchase_intake_retained_key_and_refresh(api):
    api.purchase_grocery_item.side_effect = APIResponseError(
        "api_timeout", "Purchase timeout", "purchase-trace"
    )
    ui = app()
    ui.session_state[f"pantry_{HOME.id}"] = {"snapshot": "cached"}
    section(ui, "Purchase")
    button(ui, "Start purchase").click().run()
    next(c for c in ui.checkbox if c.label == "Add purchase to pantry").check().run()
    text(ui, "Amount bought this time", "0.125001")
    text(ui, "Total purchase price (optional)", "1.25")
    next(c for c in ui.checkbox if c.label == "Explicitly allow overpurchase").check()
    button(ui, "Record purchase").click().run()
    original = record(ui)["purchase"]["payload"]
    assert (
        original.purchased_quantity == Decimal("0.125001") and original.expected_item_version == 3
    )
    assert (
        original.add_to_pantry
        and original.pantry_location_id == LOCATION.id
        and original.allow_overpurchase
    )
    assert any(t.value == "Request ID: purchase-trace" for t in ui.text)
    button(ui, "Refresh groceries").click().run()
    api.purchase_grocery_item.side_effect = None
    button(ui, "Retry purchase").click().run()
    assert api.purchase_grocery_item.call_args.args[-1] == original
    assert record(ui)["purchase"] is None
    assert ui.session_state[f"pantry_{HOME.id}"]["snapshot"] is None
    api.pantry_overview.assert_called_once_with(HOME.id)
    api.dashboard.assert_called_once_with(HOME.id)
    ui.run()
    assert api.purchase_grocery_item.call_count == 2
    assert not ui.exception


def test_unlinked_item_intake_disabled_and_empty_states(api):
    api.grocery_items.return_value = [ITEM.model_copy(update={"food_id": None})]
    ui = app()
    section(ui, "Purchase")
    button(ui, "Start purchase").click().run()
    assert next(c for c in ui.checkbox if c.label == "Add purchase to pantry").disabled
    api.grocery_lists.return_value = []
    ui = app()
    assert any("No grocery lists match" in i.value for i in ui.info)
    api.households.return_value = []
    ui = app()
    assert any(s.value == "Welcome home" for s in ui.subheader)


@pytest.mark.parametrize(
    "label,status", [("Create grocery list", "All"), ("View active grocery lists", "active")]
)
def test_dashboard_navigation(api, label, status):
    ui = app()
    ui.radio(key="page").set_value("Dashboard").run()
    button(ui, label).click().run()
    assert ui.session_state["page"] == "Grocery Lists"
    assert ui.session_state["household_id"] == str(HOME.id)
    assert ui.selectbox(key=f"groceries_{HOME.id}_filter").value == status


def test_rendering_and_validation():
    assert item_rows([ITEM])[0]["Required"] == "2.125001"
    covered = SHORTAGE.model_copy(
        update={
            "requirements": [
                SHORTAGE.requirements[0].model_copy(
                    update={"purchase_required": False, "shortage_quantity": Decimal(0)}
                )
            ]
        }
    )
    assert requirement_rows(covered)[0]["Coverage"] == "Fully covered"
    for invalid in [0.125, True, "NaN", "-1", "0.0000001"]:
        with pytest.raises(ValidationError):
            wire.ItemInput(display_name="Test", required_quantity=invalid, required_unit="g")
    with pytest.raises(ValidationError):
        wire.RequirementsInput(recipes=[SELECTION.recipes[0], SELECTION.recipes[0]])
    with pytest.raises(ValidationError):
        wire.PurchaseInput(
            purchased_quantity="1",
            purchased_unit="g",
            expected_item_version=1,
            idempotency_key="key",
            add_to_pantry=True,
        )


def test_purchase_refresh_failure_does_not_repeat_purchase(api):
    api.pantry_overview.side_effect = APIResponseError(
        "api_timeout", "Refresh failed", "refresh-trace"
    )
    ui = app()
    section(ui, "Purchase")
    button(ui, "Start purchase").click().run()
    button(ui, "Record purchase").click().run()
    assert record(ui)["purchase"] is None
    assert record(ui)["purchase_result"] == PURCHASE
    assert workspace(ui)["refresh_purchase"]
    assert any(t.value == "Request ID: refresh-trace" for t in ui.text)
    api.pantry_overview.side_effect = None
    button(ui, "Refresh groceries").click().run()
    api.purchase_grocery_item.assert_called_once()
    assert not workspace(ui)["refresh_purchase"]


def test_recipe_selection_and_pending_generation_survive_households(api):
    other = Household(id=UUID(int=98), name="Second")
    api.households.return_value = [HOME, other]
    api.generate_grocery_list.side_effect = APIResponseError("api_timeout", "Timeout", "trace")
    ui = app()
    select_recipe(ui)
    text(ui, f"Desired servings: {OWN.name}", "5.125001")
    button(ui, "Generate into selected list").click().run()
    original = record(ui)["generation"]
    ui.selectbox(key="household_id").select(str(other.id)).run()
    assert ui.session_state[f"groceries_{other.id}"]["records"][str(LIST.id)]["generation"] is None
    ui.selectbox(key="household_id").select(str(HOME.id)).run()
    assert record(ui)["generation"] == original
    assert set(ui.multiselect[0].value) == {OWN.id, SYSTEM.id}
    assert (
        next(i.value for i in ui.text_input if i.label == f"Desired servings: {OWN.name}")
        == "5.125001"
    )


def test_http_grocery_contracts():
    calls = []

    def handler(request):
        calls.append(request)
        path = request.url.path
        if request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/purchase"):
            data = PURCHASE
        elif path.endswith("/generations"):
            data = GENERATION
        elif path.endswith("/shortage-preview"):
            data = SHORTAGE
        elif path.endswith("/preview"):
            data = REQUIREMENTS
        else:
            data = ITEM if "/items" in path else LIST
        payload = data.model_dump(mode="json")
        if request.method == "GET" and path.endswith(("/items", "/grocery-lists")):
            payload = [payload]
        status = (
            201 if request.method == "POST" and path.endswith(("/items", "/grocery-lists")) else 200
        )
        return httpx.Response(status, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        api = APIClient(client=transport)
        api.grocery_lists(HOME.id)
        api.grocery_list(HOME.id, LIST.id)
        api.create_grocery_list(HOME.id, wire.ListInput(name="Weekly"))
        api.update_grocery_list(
            HOME.id, LIST.id, wire.ListUpdate(name="Weekly", expected_version=2)
        )
        api.delete_grocery_list(HOME.id, LIST.id, 2)
        api.grocery_items(HOME.id, LIST.id)
        api.grocery_item(HOME.id, LIST.id, ITEM.id)
        fields = {key: getattr(ITEM, key) for key in wire.ItemFields.model_fields}
        api.create_grocery_item(HOME.id, LIST.id, wire.ItemInput(**fields))
        api.update_grocery_item(
            HOME.id, LIST.id, ITEM.id, wire.ItemUpdate(**fields, expected_version=3)
        )
        api.delete_grocery_item(HOME.id, LIST.id, ITEM.id, 3)
        api.grocery_requirements(HOME.id, SELECTION)
        api.grocery_shortages(HOME.id, SELECTION)
        gen = wire.GenerationInput(
            recipes=SELECTION.recipes, expected_list_version=2, idempotency_key="generation-key"
        )
        api.generate_grocery_list(HOME.id, LIST.id, gen)
        purchase = wire.PurchaseInput(
            purchased_quantity="0.125001",
            purchased_unit="g",
            expected_item_version=3,
            idempotency_key="purchase-key",
            add_to_pantry=True,
            pantry_location_id=LOCATION.id,
        )
        api.purchase_grocery_item(HOME.id, LIST.id, ITEM.id, purchase)
    base = f"/v1/households/{HOME.id}/grocery-lists"
    collection = f"{base}/{LIST.id}/items"
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", base),
        ("GET", f"{base}/{LIST.id}"),
        ("POST", base),
        ("PUT", f"{base}/{LIST.id}"),
        ("DELETE", f"{base}/{LIST.id}"),
        ("GET", collection),
        ("GET", f"{collection}/{ITEM.id}"),
        ("POST", collection),
        ("PUT", f"{collection}/{ITEM.id}"),
        ("DELETE", f"{collection}/{ITEM.id}"),
        ("POST", f"/v1/households/{HOME.id}/grocery-requirements/preview"),
        ("POST", f"/v1/households/{HOME.id}/grocery-requirements/shortage-preview"),
        ("POST", f"{base}/{LIST.id}/generations"),
        ("POST", f"{collection}/{ITEM.id}/purchase"),
    ]
    assert calls[4].url.params["expected_version"] == "2"
    assert calls[9].url.params["expected_version"] == "3"
    assert json.loads(calls[8].content)["expected_version"] == 3
    assert json.loads(calls[-1].content) == purchase.model_dump(mode="json")
    assert json.loads(calls[-2].content) == gen.model_dump(mode="json")


def test_human_grocery_labels_and_guidance(api):
    ui = app()
    selector = next(box for box in ui.selectbox if box.label == "Grocery list")
    assert all(str(LIST.id) not in option for option in selector.options)
    assert LIST.name in selector.options[0]
    guide = next(e for e in ui.expander if e.label == "Shopping guide")
    assert "Create/select list" in guide.markdown[0].value
    assert "Record purchases" in guide.markdown[0].value
    assert "Item ID" not in ui.dataframe[0].value.columns
    assert "Version" not in ui.dataframe[0].value.columns
    assert any(
        str(LIST.id) in t.value
        for e in ui.expander
        if e.label == "Technical details"
        for t in e.text
    )
