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

from nourish_nest.api_client import APIClient, APIResponseError, DashboardCounts, Health, Household
from nourish_nest.pantry_client_models import (
    AdjustmentInput,
    ConsumeInput,
    LocationInput,
    LocationRecord,
    PantryItemInput,
    PantryLot,
    PantryOverview,
    StockRule,
    StockRuleInput,
    TransferInput,
)
from nourish_nest.pantry_ui import filtered_lots, inventory_rows, new_workspace
from nourish_nest.recipe_client_models import StoredFood

HOME = Household(id=UUID(int=1), name="Pantry home")
FOOD = StoredFood(id=UUID(int=2), name="Rice")
LOCATION = LocationRecord(
    id=UUID(int=3), household_id=HOME.id, name="Cupboard", location_type="cabinet"
)
TARGET = LocationRecord(id=UUID(int=4), household_id=HOME.id, name="Pantry", location_type="pantry")
LOT = PantryLot(
    id=UUID(int=5),
    household_id=HOME.id,
    food_id=FOOD.id,
    location_id=LOCATION.id,
    quantity="10.125",
    unit="g",
    status="active",
    version=4,
    purchase_date="2026-01-01",
    expiration_date="2026-09-10",
    created_at=datetime(2026, 1, 1, tzinfo=UTC),
    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
)
RULE = StockRule(
    id=UUID(int=6),
    household_id=HOME.id,
    food_id=FOOD.id,
    threshold_quantity="20",
    threshold_unit="g",
    preferred_reorder_quantity="100",
    preferred_reorder_unit="g",
)
OVERVIEW = PantryOverview(
    active_items=1,
    expired_items=0,
    depleted_items=0,
    discarded_items=0,
    low_stock_food_ids=[FOOD.id],
)


@pytest.fixture
def api(monkeypatch):
    mock = MagicMock(spec=APIClient)
    mock.__enter__.return_value = mock
    mock.health.return_value = Health(status="ok", version="test")
    mock.households.return_value = [HOME]
    mock.pantry_overview.return_value = OVERVIEW
    mock.pantry_items.return_value = [LOT]
    mock.pantry_locations.return_value = [LOCATION, TARGET]
    mock.pantry_expiring.return_value = [LOT]
    mock.pantry_expired.return_value = []
    mock.pantry_stock_rules.return_value = [RULE]
    mock.pantry_low_stock.return_value = [RULE]
    mock.stored_food.return_value = FOOD
    mock.search_foods.return_value = [FOOD]
    mock.dashboard.return_value = DashboardCounts(
        members=0,
        recipes=0,
        active_pantry_items=1,
        expiring_items=1,
        low_stock_items=1,
        active_grocery_lists=0,
    )
    monkeypatch.setattr("nourish_nest.streamlit_ui.create_api_client", lambda: mock)
    return mock


def app():
    ui = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=20
    )
    ui.session_state["page"] = "Pantry"
    return ui.run()


def button(ui, label):
    return next(b for b in ui.button if b.label == label)


def input_value(ui, label, value):
    next(t for t in ui.text_input if t.label == label).input(value)


def section(ui, name):
    return ui.radio(key=f"pantry_{HOME.id}_section").set_value(name).run()


def workspace(ui):
    return ui.session_state[f"pantry_{HOME.id}"]


def begin_action(ui, name):
    section(ui, "Actions")
    next(s for s in ui.selectbox if s.label == "Action").select(name).run()
    button(ui, "Start action").click().run()


def test_inventory_summary_and_filters(api):
    ui = app()
    assert not ui.exception
    assert [m.value for m in ui.metric] == ["1", "1", "0", "1"]
    assert ui.dataframe[0].value.iloc[0]["Quantity"] == "10.125"
    assert ui.dataframe[0].value.iloc[0]["Expiration alert"] == "Expiring soon"
    next(s for s in ui.selectbox if s.label == "Location filter").select(TARGET.id).run()
    assert not ui.dataframe
    assert any("No inventory matches" in i.value for i in ui.info)
    api.pantry_items.assert_called_once()
    button(ui, "Refresh pantry").click().run()
    assert api.pantry_items.call_count == 2


def test_api_derived_status_and_search_helpers():
    expired = LOT.model_copy(update={"id": UUID(int=9), "status": "expired"})
    depleted = LOT.model_copy(
        update={"id": UUID(int=10), "status": "depleted", "quantity": Decimal(0)}
    )
    snapshot = {
        "items": [LOT, expired, depleted],
        "locations": [LOCATION],
        "expiring": [LOT],
        "expired": [expired],
        "low_stock": [RULE],
    }
    foods = {FOOD.id: FOOD}
    assert filtered_lots(snapshot, foods, "RICE", None, "Expired") == [expired]
    assert filtered_lots(snapshot, foods, "", None, "Depleted") == [depleted]
    assert filtered_lots(snapshot, foods, "missing", None, "All") == []
    assert len(filtered_lots(snapshot, foods, "", None, "Low stock")) == 3
    rows = inventory_rows([expired, depleted], snapshot, foods)
    assert rows[0]["Status"] == "🔴 Expired" and rows[1]["Quantity"] == "0"


def test_empty_and_no_household(api):
    api.pantry_items.return_value = []
    api.pantry_stock_rules.return_value = []
    ui = app()
    assert any("No inventory matches" in i.value for i in ui.info)
    api.households.return_value = []
    ui = app()
    assert not ui.exception
    assert any(s.value == "Welcome home" for s in ui.subheader)


def test_location_create_delete_and_confirmation(api):
    ui = app()
    section(ui, "Locations")
    input_value(ui, "Location name", "New shelf")
    button(ui, "Create location").click().run()
    assert api.create_pantry_location.call_args.args == (
        HOME.id,
        LocationInput(name="New shelf", location_type="pantry"),
    )
    assert workspace(ui)["epoch"] == 1
    assert button(ui, "Delete location").disabled
    ui.checkbox[0].check().run()
    button(ui, "Delete location").click().run()
    api.delete_pantry_location.assert_called_once_with(HOME.id, LOCATION.id)
    assert not ui.exception


def test_add_lot_decimal_payload_and_state_reset(api):
    ui = app()
    section(ui, "Add item")
    button(ui, "Search foods").click().run()
    input_value(ui, "Quantity", "0.125")
    button(ui, "Add pantry item").click().run()
    data = api.create_pantry_item.call_args.args[1]
    assert data.quantity == Decimal("0.125") and data.location_id == LOCATION.id
    assert data.purchase_date is None and data.expiration_date is None
    assert workspace(ui)["search"] == {} and workspace(ui)["epoch"] == 1
    ui.run()
    api.create_pantry_item.assert_called_once()
    assert not ui.exception


@pytest.mark.parametrize(
    "operation,method",
    [
        ("Increase quantity", "adjust_pantry_item"),
        ("Consume (FEFO)", "consume_pantry"),
        ("Transfer whole lot", "transfer_pantry"),
        ("Discard", "discard_pantry_item"),
    ],
)
def test_inventory_actions(api, operation, method):
    ui = app()
    begin_action(ui, operation)
    key = workspace(ui)["action"]["key"]
    if operation == "Discard":
        button(ui, "Submit action").click().run()
        getattr(api, method).assert_not_called()
        ui.checkbox[0].check()
    button(ui, "Submit action").click().run()
    data = getattr(api, method).call_args.args[-1]
    assert data.idempotency_key == key
    if operation != "Consume (FEFO)":
        assert data.version == LOT.version
    else:
        assert "version" not in data.model_dump()
    assert workspace(ui)["action"] is None
    ui.run()
    getattr(api, method).assert_called_once()
    assert not ui.exception


@pytest.mark.parametrize(
    "code",
    [
        "stale_inventory_version",
        "insufficient_inventory",
        "incompatible_units",
        "unsupported_conversion",
        "expired_inventory",
        "duplicate_idempotency_key",
    ],
)
def test_action_error_and_retry_key_retention(api, code):
    api.adjust_pantry_item.side_effect = APIResponseError(
        code, "Pantry action failed", "pantry-trace"
    )
    ui = app()
    begin_action(ui, "Increase quantity")
    key = workspace(ui)["action"]["key"]
    button(ui, "Submit action").click().run()
    assert any(t.value == "Request ID: pantry-trace" for t in ui.text)
    assert any(t.value == f"Error code: {code}" for t in ui.text)
    button(ui, "Refresh pantry").click().run()
    assert workspace(ui)["action"]["key"] == key
    button(ui, "Retry saved action").click().run()
    first, second = api.adjust_pantry_item.call_args_list
    assert first.args[-1] == second.args[-1]
    button(ui, "Reset action with current stock").click().run()
    assert workspace(ui)["action"] is None
    button(ui, "Start action").click().run()
    assert workspace(ui)["action"]["key"] != key
    assert not ui.exception


def test_location_not_empty_error(api):
    api.delete_pantry_location.side_effect = APIResponseError(
        "pantry_location_not_empty", "Location not empty", "location-trace"
    )
    ui = app()
    section(ui, "Locations")
    ui.checkbox[0].check().run()
    button(ui, "Delete location").click().run()
    assert any(e.value == "Location not empty" for e in ui.error)
    assert any(t.value == "Request ID: location-trace" for t in ui.text)


def test_low_stock_update_and_history_boundary(api):
    ui = app()
    section(ui, "Low-stock rules")
    assert ui.dataframe[0].value.iloc[0]["Stock status"] == "Low stock"
    button(ui, "Search foods").click().run()
    input_value(ui, "Low-stock threshold", "0.125")
    button(ui, "Save stock rule").click().run()
    data = api.save_pantry_stock_rule.call_args.args[-1]
    assert data.threshold_quantity == Decimal("0.125")
    section(ui, "History")
    assert any("no endpoint to read" in i.value for i in ui.info)
    assert not ui.exception


@pytest.mark.parametrize(
    "label,section_name,view",
    [
        ("Add pantry item", "Add item", None),
        ("View expiring items", "Inventory", "Expiring soon"),
        ("View low-stock items", "Inventory", "Low stock"),
    ],
)
def test_dashboard_navigation(api, label, section_name, view):
    ui = app()
    ui.radio(key="page").set_value("Dashboard").run()
    button(ui, label).click().run()
    assert ui.session_state["page"] == "Pantry"
    assert ui.session_state["household_id"] == str(HOME.id)
    assert ui.radio(key=f"pantry_{HOME.id}_section").value == section_name
    if view:
        assert ui.selectbox(key=f"pantry_{HOME.id}_inventory_view").value == view
    assert not ui.exception


@pytest.mark.parametrize("quantity", ["0", "-1", "0.0001", "NaN", "Infinity"])
def test_positive_decimal_validation(quantity):
    with pytest.raises(ValidationError):
        PantryItemInput(food_id=FOOD.id, location_id=LOCATION.id, quantity=quantity, unit="g")


def test_household_workspace_preservation(api):
    other = Household(id=UUID(int=99), name="Second home")
    api.households.return_value = [HOME, other]
    ui = app()
    begin_action(ui, "Increase quantity")
    key = workspace(ui)["action"]["key"]
    ui.selectbox(key="household_id").select(str(other.id)).run()
    assert ui.session_state[f"pantry_{other.id}"]["action"] is None
    ui.selectbox(key="household_id").select(str(HOME.id)).run()
    assert workspace(ui)["action"]["key"] == key
    assert new_workspace()["action"] is None


def test_http_pantry_contracts():
    calls = []

    def handler(request):
        calls.append(request)
        path = request.url.path
        if request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/summary"):
            data = OVERVIEW.model_dump(mode="json")
        elif path.endswith("/locations"):
            data = LOCATION.model_dump(mode="json")
            if request.method == "GET":
                data = [data]
        elif "/foods/" in path:
            data = FOOD.model_dump(mode="json")
        elif "/stock-rules" in path or path.endswith("/low-stock"):
            data = RULE.model_dump(mode="json")
            if request.method == "GET":
                data = [data]
        else:
            data = LOT.model_dump(mode="json")
            if request.method == "GET" or path.endswith("/consume"):
                data = [data]
        return httpx.Response(
            201 if request.method == "POST" and path.endswith(("/locations", "/items")) else 200,
            json=data,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        api = APIClient(client=client)
        api.stored_food(FOOD.id)
        api.pantry_locations(HOME.id)
        api.create_pantry_location(HOME.id, LocationInput(name="Cupboard", location_type="cabinet"))
        api.delete_pantry_location(HOME.id, LOCATION.id)
        api.pantry_items(HOME.id)
        api.pantry_overview(HOME.id)
        api.pantry_expiring(HOME.id)
        api.pantry_expired(HOME.id)
        api.pantry_low_stock(HOME.id)
        api.pantry_stock_rules(HOME.id)
        api.save_pantry_stock_rule(HOME.id, FOOD.id, StockRuleInput(**RULE.model_dump()))
        api.create_pantry_item(
            HOME.id,
            PantryItemInput(food_id=FOOD.id, location_id=LOCATION.id, quantity="0.125", unit="g"),
        )
        adjustment = AdjustmentInput(
            quantity_change="0.125", unit="g", version=4, idempotency_key="stable"
        )
        api.adjust_pantry_item(HOME.id, LOT.id, adjustment)
        api.discard_pantry_item(HOME.id, LOT.id, adjustment)
        api.consume_pantry(
            HOME.id,
            ConsumeInput(food_id=FOOD.id, quantity="0.125", unit="g", idempotency_key="consume"),
        )
        api.transfer_pantry(
            HOME.id,
            TransferInput(
                item_id=LOT.id, target_location_id=TARGET.id, version=4, idempotency_key="transfer"
            ),
        )
    base = f"/v1/households/{HOME.id}/pantry"
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", f"/v1/foods/{FOOD.id}"),
        ("GET", f"{base}/locations"),
        ("POST", f"{base}/locations"),
        ("DELETE", f"{base}/locations/{LOCATION.id}"),
        ("GET", f"{base}/items"),
        ("GET", f"{base}/summary"),
        ("GET", f"{base}/expiring"),
        ("GET", f"{base}/expired"),
        ("GET", f"{base}/low-stock"),
        ("GET", f"{base}/stock-rules"),
        ("PUT", f"{base}/stock-rules/{FOOD.id}"),
        ("POST", f"{base}/items"),
        ("POST", f"{base}/items/{LOT.id}/adjust"),
        ("POST", f"{base}/items/{LOT.id}/discard"),
        ("POST", f"{base}/consume"),
        ("POST", f"{base}/transfer"),
    ]
    assert json.loads(calls[-4].content) == adjustment.model_dump(mode="json")
    assert json.loads(calls[-1].content)["version"] == 4
    assert "version" not in json.loads(calls[-2].content)
    assert json.loads(calls[-5].content)["quantity"] == "0.125"


@pytest.mark.parametrize(
    "method",
    ["pantry_items", "pantry_overview", "pantry_expiring", "pantry_expired", "pantry_low_stock"],
)
def test_legacy_pantry_reads_are_not_retried(method):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            503, json={"code": "unavailable", "message": "Retry later", "request_id": "read-trace"}
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as transport,
        pytest.raises(APIResponseError),
    ):
        getattr(APIClient(client=transport), method)(HOME.id)
    assert len(calls) == 1
