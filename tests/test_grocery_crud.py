from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from nourish_nest.api import app
from nourish_nest.database import Base, get_db
from nourish_nest.grocery_schemas import (
    GroceryItemCreate,
    GroceryItemUpdate,
    GroceryListCreate,
    GroceryListUpdate,
)
from nourish_nest.grocery_services import GroceryService, StaleGroceryVersionError
from nourish_nest.models import GroceryList, GroceryListItem, Household
from nourish_nest.services import NotFoundError


@pytest.fixture
def grocery_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'crud.db'}",
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def setup_list(client):
    household = client.post("/v1/households", json={"name": "Home"}).json()["id"]
    base = f"/v1/households/{household}/grocery-lists"
    response = client.post(base, json={"name": "Weekly"})
    assert response.status_code == 201, response.text
    listing = response.json()
    return base, f"{base}/{listing['id']}", listing


def item_payload(**overrides):
    return {"display_name": "Rice", "required_quantity": "1.234567", "required_unit": "kg",
            **overrides}


def test_crud_lifecycle_and_cascade(grocery_client):
    client, sessions = grocery_client
    base, path, listing = setup_list(client)
    assert client.get(base).json() == [listing]
    assert client.get(path).json() == listing
    changed = client.put(path, json={"name": "Weekend", "status": "active", "expected_version": 1})
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    created = client.post(f"{path}/items", json=item_payload())
    assert created.status_code == 201, created.text
    item = created.json()
    assert Decimal(item["required_quantity"]) == Decimal("1.234567")
    assert item["version"] == 1 and item["checked"] is False
    item_path = f"{path}/items/{item['id']}"
    assert client.get(item_path).json() == item
    assert client.get(f"{path}/items").json() == [item]
    updated = client.put(item_path, json=item_payload(
        purchased_quantity="1.234567", checked=True, expected_version=1))
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2 and updated.json()["checked"] is True
    assert client.delete(item_path, params={"expected_version": 2}).status_code == 204
    assert client.get(item_path).status_code == 404
    assert client.get(f"{path}/items").json() == []
    child = client.post(f"{path}/items", json=item_payload()).json()
    assert client.delete(path, params={"expected_version": 2}).status_code == 204
    assert client.get(path).status_code == 404
    assert client.get(base).json() == []
    with sessions() as session:
        assert session.get(GroceryListItem, UUID(child["id"])) is None


def test_household_and_parent_list_isolation(grocery_client):
    client, _ = grocery_client
    base, path, listing = setup_list(client)
    other_base, other_path, _ = setup_list(client)
    item = client.post(f"{path}/items", json=item_payload()).json()
    foreign_path = f"{other_base}/{listing['id']}"
    calls = [
        ("get", foreign_path, {}),
        ("put", foreign_path, {"json": {"name": "Wrong", "expected_version": 1}}),
        ("delete", foreign_path, {"params": {"expected_version": 1}}),
        ("get", f"{foreign_path}/items", {}),
        ("post", f"{foreign_path}/items", {"json": item_payload()}),
    ]
    sibling = client.post(base, json={"name": "Sibling"}).json()
    for wrong_parent in [foreign_path, other_path, f"{base}/{sibling['id']}"]:
        wrong_item = f"{wrong_parent}/items/{item['id']}"
        calls.extend([
            ("get", wrong_item, {}),
            ("put", wrong_item, {"json": item_payload(expected_version=1)}),
            ("delete", wrong_item, {"params": {"expected_version": 1}}),
        ])
    for method, url, kwargs in calls:
        response = client.request(method, url, headers={"x-request-id": "scope-1"}, **kwargs)
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "not_found"
        assert response.json()["request_id"] == response.headers["x-request-id"] == "scope-1"
        assert set(response.json()) == {"code", "message", "request_id"}
    assert client.get(f"{path}/items/{item['id']}").json() == item
    assert all(row["id"] != listing["id"] for row in client.get(other_base).json())
    missing = f"/v1/households/{uuid4()}/grocery-lists"
    assert client.get(missing).status_code == 404
    assert client.post(missing, json={"name": "Missing"}).status_code == 404


@pytest.mark.parametrize("item", [False, True])
def test_expected_version_and_stale_envelope(grocery_client, item):
    client, _ = grocery_client
    _, path, _ = setup_list(client)
    payload = {"name": "Weekly"}
    if item:
        record = client.post(f"{path}/items", json=item_payload()).json()
        path = f"{path}/items/{record['id']}"
        payload = item_payload()
    assert client.put(path, json=payload).status_code == 422
    assert client.delete(path).status_code == 422
    assert client.delete(path, params={"expected_version": 0}).status_code == 422
    # Identical PUT still increments the version and checks for concurrent writes.
    assert client.put(path, json={**payload, "expected_version": 1}).json()["version"] == 2
    for method, kwargs in [
        ("put", {"json": {**payload, "expected_version": 1}}),
        ("delete", {"params": {"expected_version": 1}}),
    ]:
        response = client.request(method, path, headers={"x-request-id": "stale-1"}, **kwargs)
        assert response.status_code == 409
        assert response.json() == {"code": "stale_grocery_version",
                                   "message": "Grocery record version is stale", "request_id": "stale-1"}
        assert response.headers["x-request-id"] == "stale-1"
    assert client.get(path).json()["version"] == 2


@pytest.mark.parametrize("values", [
    {"purchased_quantity": "2"},
    {"checked": True},
    {"required_quantity": "-1"},
    {"purchased_quantity": "-1"},
    {"required_quantity": "NaN"},
    {"required_quantity": "0.0000001"},
    {"required_quantity": "1000000000000"},
    {"required_quantity": 1.25},
    {"required_unit": " "},
])
def test_quantity_validation_preserves_records(grocery_client, values):
    client, _ = grocery_client
    _, path, _ = setup_list(client)
    item = client.post(f"{path}/items", json=item_payload()).json()
    item_path = f"{path}/items/{item['id']}"
    for method, url, payload in [
        ("post", f"{path}/items", item_payload(**values)),
        ("put", item_path, item_payload(expected_version=1, **values)),
    ]:
        response = client.request(method, url, json=payload, headers={"x-request-id": "invalid-1"})
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "invalid_request"
        assert response.json()["request_id"] == response.headers["x-request-id"] == "invalid-1"
    assert client.get(item_path).json() == item
    assert len(client.get(f"{path}/items").json()) == 1


def test_food_references_and_zero_quantity(grocery_client):
    client, _ = grocery_client
    _, path, _ = setup_list(client)
    item = client.post(f"{path}/items", json=item_payload()).json()
    item_path = f"{path}/items/{item['id']}"
    for method, url, payload in [
        ("post", f"{path}/items", item_payload(food_id=str(uuid4()))),
        ("put", item_path, item_payload(food_id=str(uuid4()), expected_version=1)),
    ]:
        response = client.request(method, url, json=payload)
        assert response.status_code == 404
        assert response.json()["message"] == "Food not found"
    assert client.get(item_path).json() == item
    food = client.post("/v1/foods", json={"name": "Rice", "source_type": "manual",
                                       "serving_quantity": "1", "serving_unit": "kg"}).json()
    response = client.put(item_path, json=item_payload(food_id=food["id"], expected_version=1))
    assert response.status_code == 200
    assert response.json()["food_id"] == food["id"]
    response = client.put(item_path, json=item_payload(
        required_quantity="0", purchased_quantity="0", checked=True, expected_version=2))
    assert response.status_code == 200
    assert response.json()["food_id"] is None
    assert response.json()["checked"] is True


@pytest.mark.parametrize("operation", [
    "create_list", "update_list", "delete_list", "create_item", "update_item", "delete_item",
])
@pytest.mark.parametrize("failure", [SQLAlchemyError, StaleDataError])
def test_write_failure_rolls_back_and_session_is_reusable(grocery_client, monkeypatch, operation, failure):
    _, sessions = grocery_client
    with sessions() as session:
        household = Household(name="Home")
        session.add(household)
        session.commit()
        service = GroceryService(session)
        listing = service.create_list(household.id, GroceryListCreate(name="Weekly"))
        item = service.create_item(household.id, listing.id, GroceryItemCreate(**item_payload()))
        args = {
            "create_list": (household.id, GroceryListCreate(name="Failed")),
            "update_list": (household.id, listing.id, GroceryListUpdate(name="Failed", expected_version=1)),
            "delete_list": (household.id, listing.id, 1),
            "create_item": (household.id, listing.id, GroceryItemCreate(**item_payload())),
            "update_item": (household.id, listing.id, item.id,
                            GroceryItemUpdate(**item_payload(display_name="Failed", expected_version=1))),
            "delete_item": (household.id, listing.id, item.id, 1),
        }

        def fail_commit():
            session.flush()
            raise failure("Injected write failure")

        with monkeypatch.context() as patch:
            patch.setattr(session, "commit", fail_commit)
            expected = StaleGroceryVersionError if failure is StaleDataError else SQLAlchemyError
            with pytest.raises(expected):
                getattr(service, operation)(*args[operation])
        assert not session.in_transaction()
        assert len(session.scalars(select(GroceryList)).all()) == 1
        assert len(session.scalars(select(GroceryListItem)).all()) == 1
        assert service.get_list(household.id, listing.id).name == "Weekly"
        assert service.get_item(household.id, listing.id, item.id).display_name == "Rice"
        assert service.get_list(household.id, listing.id).version == 1
        assert service.get_item(household.id, listing.id, item.id).version == 1
        service.update_list(household.id, listing.id, GroceryListUpdate(name="Recovered", expected_version=1))


def test_lookup_failure_rolls_back_pending_work(grocery_client):
    _, sessions = grocery_client
    with sessions() as session:
        pending = Household(name="Uncommitted")
        session.add(pending)
        with pytest.raises(NotFoundError):
            GroceryService(session).create_list(uuid4(), GroceryListCreate(name="Missing"))
        assert not session.in_transaction()
        assert session.scalar(select(Household)) is None


@pytest.mark.parametrize("item_record", [False, True])
def test_database_detects_race_after_version_read(grocery_client, item_record):
    client, sessions = grocery_client
    _, path, listing = setup_list(client)
    item = client.post(f"{path}/items", json=item_payload()).json()
    household_id, list_id = UUID(listing["household_id"]), UUID(listing["id"])
    model = GroceryListItem if item_record else GroceryList
    record_id = UUID(item["id"]) if item_record else list_id
    with sessions() as stale, sessions() as winner:
        old = stale.get(model, record_id)
        current = winner.get(model, record_id)
        field = "display_name" if item_record else "name"
        setattr(current, field, "Winner")
        winner.commit()
        assert old.version == 1
        service = GroceryService(stale)
        with pytest.raises(StaleGroceryVersionError):
            if item_record:
                service.update_item(household_id, list_id, record_id,
                                    GroceryItemUpdate(**item_payload(expected_version=1)))
            else:
                service.update_list(household_id, list_id,
                                    GroceryListUpdate(name="Loser", expected_version=1))
        assert not stale.in_transaction()
        stale.refresh(old)
        assert getattr(old, field) == "Winner" and old.version == 2


def test_database_error_uses_existing_envelope(grocery_client, monkeypatch):
    client, sessions = grocery_client
    base, _, _ = setup_list(client)

    def fail_flush(session, context, instances):
        raise SQLAlchemyError("Private database details")

    with sessions() as session:
        event.listen(session, "before_flush", fail_flush)

        def override_db():
            yield session

        monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
        response = client.post(base, json={"name": "Failed"}, headers={"x-request-id": "db-1"})
        assert response.status_code == 500
        assert response.json() == {"code": "internal_error", "message": "Internal server error",
                                   "request_id": "db-1"}
        assert response.headers["x-request-id"] == "db-1"
        assert not session.in_transaction()
