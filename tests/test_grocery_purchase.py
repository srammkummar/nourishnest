from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from io import StringIO
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_grocery_crud import grocery_client  # noqa: F401

from alembic import command
from nourish_nest.config import get_settings
from nourish_nest.database import Base
from nourish_nest.grocery_purchase_repositories import GroceryPurchaseRepository
from nourish_nest.grocery_purchase_schemas import GroceryPurchaseRequest
from nourish_nest.grocery_purchase_services import GroceryPurchaseService
from nourish_nest.models import (
    GroceryList,
    GroceryListItem,
    GroceryPurchaseEvent,
    Household,
    PantryItem,
    PantryTransaction,
    PantryTransactionType,
)


@pytest.fixture
def purchase_data(request):
    client, sessions = request.getfixturevalue("grocery_client")
    home = client.post("/v1/households", json={"name": "Home"}).json()["id"]
    food = client.post("/v1/foods", json={"name": "Rice", "serving_quantity": "1", "serving_unit": "g"}).json()["id"]
    location = client.post(f"/v1/households/{home}/pantry/locations",
                           json={"name": "Pantry", "location_type": "pantry"}).json()["id"]
    listing = client.post(f"/v1/households/{home}/grocery-lists", json={"name": "Weekly"}).json()["id"]
    item = client.post(f"/v1/households/{home}/grocery-lists/{listing}/items", json={
        "display_name": "Rice", "food_id": food, "required_quantity": "1", "required_unit": "kg",
    }).json()["id"]
    return client, sessions, home, listing, item, location


def purchase(data, **values):
    client, _, home, listing, item, _ = data
    return client.post(f"/v1/households/{home}/grocery-lists/{listing}/items/{item}/purchase",
                       json=payload(**values), headers={"x-request-id": "purchase-1"})


def payload(**values):
    return {"purchased_quantity": "0.25", "purchased_unit": "kg", "expected_item_version": 1,
            "idempotency_key": "purchase-1", "add_to_pantry": False, **values}


def saved_counts(session):
    return [len(session.scalars(select(model)).all())
            for model in [GroceryPurchaseEvent, PantryItem, PantryTransaction]]


def test_partial_complete_and_historical_replay(purchase_data):
    first = purchase(purchase_data)
    assert first.status_code == 200, first.text
    assert first.json()["checked"] is False
    assert Decimal(first.json()["purchased_total"]) == Decimal("0.25")
    assert first.json()["item_version"] == 2 and first.json()["grocery_list_version"] == 2
    assert first.json()["grocery_list_status"] == "draft"
    second = purchase(purchase_data, purchased_quantity="0.75", expected_item_version=2, idempotency_key="purchase-2")
    assert second.status_code == 200, second.text
    assert second.json()["checked"] is True and second.json()["grocery_list_status"] == "completed"
    assert second.json()["item_version"] == 3 and second.json()["grocery_list_version"] == 3
    replay = purchase(purchase_data)
    assert replay.json() == {**first.json(), "replayed": True}
    with purchase_data[1]() as session:
        assert saved_counts(session) == [2, 0, 0]


def test_overpurchase_allowance_remains_readable(purchase_data):
    response = purchase(purchase_data, purchased_quantity="1.1")
    assert response.status_code == 409 and response.json()["code"] == "overpurchase_not_allowed"
    response = purchase(purchase_data, purchased_quantity="1.1", allow_overpurchase=True)
    assert response.status_code == 200, response.text
    assert response.json()["checked"] is True
    client, _, home, listing, item, _ = purchase_data
    response = client.get(f"/v1/households/{home}/grocery-lists/{listing}/items/{item}")
    assert response.status_code == 200
    assert Decimal(response.json()["purchased_quantity"]) == Decimal("1.1")
    assert Decimal(response.json()["required_quantity"]) == Decimal(1)


def test_intake_conversion_price_and_no_duplicate_stock(purchase_data):
    _, sessions, home, listing, item, location = purchase_data
    values = {"purchased_quantity": "125", "purchased_unit": "g", "add_to_pantry": True,
              "pantry_location_id": location, "expiration_date": "2099-01-01", "purchase_price": "2.345678"}
    first = purchase(purchase_data, **values)
    assert first.status_code == 200, first.text
    result = first.json()
    assert Decimal(result["item_quantity"]) == Decimal("0.125")
    assert Decimal(result["purchase_price"]) == Decimal("2.345678") and result["currency"] == "USD"
    assert purchase(purchase_data, **values).json() == {**result, "replayed": True}
    with sessions() as session:
        assert saved_counts(session) == [1, 1, 1]
        lot = session.get(PantryItem, UUID(result["pantry_item_id"]))
        assert lot.household_id == UUID(home) and lot.location_id == UUID(location)
        assert lot.quantity == lot.canonical_quantity == Decimal(125)
        assert lot.unit == lot.canonical_unit == "g" and lot.expiration_date == date(2099, 1, 1)
        transaction = session.get(PantryTransaction, UUID(result["pantry_transaction_id"]))
        assert transaction.transaction_type == PantryTransactionType.RESTOCK
        assert transaction.quantity_change == Decimal(125) and transaction.pantry_item_id == lot.id
        audit = session.get(GroceryPurchaseEvent, UUID(result["purchase_event_id"]))
        assert audit.grocery_list_id == UUID(listing) and audit.grocery_list_item_id == UUID(item)
        assert audit.pantry_item == lot and audit.pantry_transaction == transaction


def test_manual_item_without_food(purchase_data):
    _, sessions, _, _, item, location = purchase_data
    with sessions() as session:
        row = session.get(GroceryListItem, UUID(item))
        row.food_id = None
        session.commit()
        version = row.version
    response = purchase(purchase_data, expected_item_version=version, add_to_pantry=True, pantry_location_id=location)
    assert response.status_code == 422 and response.json()["code"] == "missing_food_reference"
    assert purchase(purchase_data, expected_item_version=version).status_code == 200


@pytest.mark.parametrize(("unit", "code"), [("l", "incompatible_units"), ("pinch", "unsupported_conversion")])
def test_invalid_conversion(purchase_data, unit, code):
    response = purchase(purchase_data, purchased_unit=unit)
    assert response.status_code == 422 and response.json()["code"] == code
    assert response.json()["request_id"] == response.headers["x-request-id"] == "purchase-1"
    with purchase_data[1]() as session:
        assert saved_counts(session) == [0, 0, 0]


def test_ownership_and_location_validation(purchase_data):
    client, _, home, listing, item, location = purchase_data
    other = client.post("/v1/households", json={"name": "Other"}).json()["id"]
    wrong_location = client.post(f"/v1/households/{other}/pantry/locations",
                                 json={"name": "Private", "location_type": "pantry"}).json()["id"]
    for owner, target_list, target_item in [(other, listing, item), (str(uuid4()), listing, item),
                                           (home, str(uuid4()), item), (home, listing, str(uuid4()))]:
        response = client.post(f"/v1/households/{owner}/grocery-lists/{target_list}/items/{target_item}/purchase",
                               json=payload())
        assert response.status_code == 404 and response.json()["code"] == "not_found"
    for target in [wrong_location, str(uuid4())]:
        response = purchase(purchase_data, pantry_location_id=target, add_to_pantry=True)
        assert response.status_code == 404
        assert response.json()["message"] == "Pantry location not found"
    assert purchase(purchase_data, add_to_pantry=True).status_code == 422
    assert purchase(purchase_data, add_to_pantry=True, pantry_location_id=location).status_code == 200


def test_stale_and_conflicting_replay(purchase_data):
    assert purchase(purchase_data).status_code == 200
    stale = purchase(purchase_data, idempotency_key="new")
    assert stale.status_code == 409 and stale.json()["code"] == "stale_grocery_version"
    conflict = purchase(purchase_data, purchased_quantity="0.5")
    assert conflict.status_code == 409 and conflict.json()["code"] == "idempotency_conflict"
    assert conflict.json()["request_id"] == conflict.headers["x-request-id"] == "purchase-1"
    assert set(conflict.json()) == {"code", "message", "request_id"}
    assert purchase(purchase_data, purchased_quantity="0.250000").json()["replayed"] is True


@pytest.mark.parametrize("values", [
    {"purchased_quantity": "0"}, {"purchased_quantity": "-1"}, {"purchased_quantity": 0.25},
    {"purchased_quantity": "NaN"}, {"purchase_price": "-1"}, {"purchase_price": 1.5},
    {"idempotency_key": " "}, {"idempotency_key": "x" * 201}, {"expected_item_version": 0},
])
def test_request_validation(purchase_data, values):
    assert purchase(purchase_data, **values).status_code == 422


def test_completion_requires_all_items(purchase_data):
    client, _, home, listing, item, location = purchase_data
    other_item = client.post(f"/v1/households/{home}/grocery-lists/{listing}/items", json={
        "display_name": "Other", "required_quantity": "1", "required_unit": "item",
    }).json()["id"]
    first = purchase(purchase_data, purchased_quantity="1")
    assert first.json()["checked"] is True and first.json()["grocery_list_status"] != "completed"
    second_data = (client, purchase_data[1], home, listing, other_item, location)
    second = purchase(second_data, purchased_quantity="1", purchased_unit="item")
    assert second.json()["grocery_list_status"] == "completed"
    assert client.get(f"/v1/households/{home}/grocery-lists/{listing}/items/{item}").json()["checked"] is True


def test_exact_decimal_and_pantry_response_precision(purchase_data):
    client, _, home, _, _, location = purchase_data
    response = purchase(purchase_data, purchased_quantity="0.123456", add_to_pantry=True, pantry_location_id=location)
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["purchased_total"]) == Decimal("0.123456")
    lot = client.get(f"/v1/households/{home}/pantry/items/{response.json()['pantry_item_id']}")
    assert lot.status_code == 200, lot.text
    assert Decimal(lot.json()["quantity"]) == Decimal("0.123456")
    second = purchase(purchase_data, purchased_quantity="0.000001", expected_item_version=2, idempotency_key="second")
    assert Decimal(second.json()["purchased_total"]) == Decimal("0.123457")


@pytest.mark.parametrize("stage", ["grocery_lists", "grocery_list_items", "pantry_items",
                                    "pantry_transactions", "grocery_purchase_events", "commit"])
def test_atomic_rollback(purchase_data, monkeypatch, stage):
    _, sessions, home, listing, item, location = purchase_data
    with sessions() as session:
        db = session.get_bind()

        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith((f"UPDATE {stage} ", f"INSERT INTO {stage} ")):
                raise SQLAlchemyError("Injected failure")

        def fail_commit():
            raise SQLAlchemyError("Injected commit failure")

        if stage == "commit":
            monkeypatch.setattr(session, "commit", fail_commit)
        event.listen(db, "after_cursor_execute", fail)
        try:
            with pytest.raises(SQLAlchemyError):
                GroceryPurchaseService(session).purchase(UUID(home), UUID(listing), UUID(item),
                    GroceryPurchaseRequest(**payload(purchased_quantity="1", add_to_pantry=True, pantry_location_id=location)))
        finally:
            event.remove(db, "after_cursor_execute", fail)
        assert not session.in_transaction()
        assert saved_counts(session) == [0, 0, 0]
        row = session.get(GroceryListItem, UUID(item))
        assert row.purchased_quantity == Decimal(0) and row.version == 1 and not row.checked
        assert session.get(GroceryList, UUID(listing)).version == 1
        assert session.get(GroceryList, UUID(listing)).status.value == "draft"


def test_database_uniqueness_recovery(purchase_data, monkeypatch):
    _, sessions, home, listing, item, location = purchase_data
    values = payload(add_to_pantry=True, pantry_location_id=location)
    winner = purchase(purchase_data, **values).json()
    with sessions() as session:
        # Reproduce a pre-query that missed a committed winner while the item version was stale.
        row = session.get(GroceryListItem, UUID(item))
        row.version = 1
        session.commit()
        service = GroceryPurchaseService(session)
        original = service.repo.event
        calls = 0

        def miss_once(*args):
            nonlocal calls
            calls += 1
            return None if calls == 1 else original(*args)

        monkeypatch.setattr(service.repo, "event", miss_once)
        result = service.purchase(UUID(home), UUID(listing), UUID(item), GroceryPurchaseRequest(**values))
        assert result.replayed and str(result.purchase_event_id) == winner["purchase_event_id"]
        assert calls == 2 and saved_counts(session) == [1, 1, 1]
        assert session.get(GroceryListItem, UUID(item)).purchased_quantity == Decimal("0.25")


def test_simultaneous_duplicate_purchase(purchase_data, monkeypatch):
    _, sessions, home, listing, item, location = purchase_data
    barrier = Barrier(2)
    original = GroceryPurchaseRepository.get_list

    def synchronized(repo, owner, target):
        result = original(repo, owner, target)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(GroceryPurchaseRepository, "get_list", synchronized)

    def attempt(_):
        with sessions() as session:
            return GroceryPurchaseService(session).purchase(UUID(home), UUID(listing), UUID(item),
                GroceryPurchaseRequest(**payload(add_to_pantry=True, pantry_location_id=location)))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [0, 1]))
    assert sorted(row.replayed for row in results) == [False, True]
    assert results[0].purchase_event_id == results[1].purchase_event_id
    with sessions() as session:
        assert saved_counts(session) == [1, 1, 1]


def test_migration_purchase_roundtrip_and_postgresql(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'purchase.db'}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260908_0006")
    db = create_engine(url)

    @event.listens_for(db, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    with Session(db) as session:
        household = Household(name="Existing")
        listing = GroceryList(household=household, name="Existing")
        item = GroceryListItem(grocery_list=listing, display_name="Manual", required_quantity=Decimal(1), required_unit="kg")
        session.add(item)
        session.commit()
        ids = household.id, listing.id, item.id
    command.upgrade(config, "head")
    with db.connect() as connection:
        context = MigrationContext.configure(connection, opts={"include_object":
            lambda obj, name, kind, reflected, compare_to: name == "grocery_purchase_events" if kind == "table" else True})
        assert compare_metadata(context, Base.metadata) == []
    with Session(db) as session:
        result = GroceryPurchaseService(session).purchase(*ids, GroceryPurchaseRequest(**payload()))
        assert result.purchased_total == Decimal("0.25")
    # Test removal of purchase migration 0007 even when later revisions exist.
    command.downgrade(config, "20260908_0006")
    assert "grocery_purchase_events" not in inspect(db).get_table_names()
    with Session(db) as session:
        assert session.get(GroceryListItem, ids[2]).purchased_quantity == Decimal("0.25")
    command.upgrade(config, "head")
    with Session(db) as session:
        assert session.scalar(select(GroceryPurchaseEvent)) is None
    db.dispose()
    monkeypatch.setattr(get_settings(), "database_url", "postgresql://localhost/nourishnest")
    output = StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "20260908_0006:20260909_0007", sql=True)
    ddl = output.getvalue()
    assert "NUMERIC(18, 6)" in ddl and "UUID" in ddl
    assert "UNIQUE (household_id, grocery_list_id, grocery_list_item_id, idempotency_key)" in ddl


def test_replay_after_pantry_lot_deletion_does_not_recreate_stock(purchase_data):
    _, sessions, _, _, _, location = purchase_data
    values = {"add_to_pantry": True, "pantry_location_id": location}
    first = purchase(purchase_data, **values).json()
    with sessions() as session:
        session.delete(session.get(PantryItem, UUID(first["pantry_item_id"])))
        session.commit()
    replay = purchase(purchase_data, **values)
    assert replay.status_code == 200
    assert replay.json()["replayed"] and replay.json()["purchase_event_id"] == first["purchase_event_id"]
    assert replay.json()["pantry_item_id"] is None and replay.json()["pantry_transaction_id"] is None
    with sessions() as session:
        assert saved_counts(session) == [1, 0, 0]


def test_concurrent_different_items_complete_list(purchase_data, monkeypatch):
    client, sessions, home, listing, first_item, _ = purchase_data
    second_item = client.post(f"/v1/households/{home}/grocery-lists/{listing}/items", json={
        "display_name": "Second", "required_quantity": "1", "required_unit": "kg",
    }).json()["id"]
    barrier = Barrier(2)
    seen = set()
    original = GroceryPurchaseRepository.get_list

    def synchronized(repo, owner, target):
        result = original(repo, owner, target)
        if id(repo.session) not in seen:
            seen.add(id(repo.session))
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(GroceryPurchaseRepository, "get_list", synchronized)

    def attempt(item):
        with sessions() as session:
            return GroceryPurchaseService(session).purchase(UUID(home), UUID(listing), UUID(item),
                GroceryPurchaseRequest(**payload(purchased_quantity="1")))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [first_item, second_item]))
    assert all(result.checked and not result.replayed for result in results)
    with sessions() as session:
        row = session.get(GroceryList, UUID(listing))
        assert row.status.value == "completed" and row.version == 3
        assert saved_counts(session) == [2, 0, 0]
