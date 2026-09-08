from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from nourish_nest.api import app
from nourish_nest.database import Base, get_db
from nourish_nest.models import PantryItem, PantryTransaction, PantryTransactionType
from nourish_nest.pantry_schemas import PantryAdjustment, PantryTransferRequest
from nourish_nest.pantry_services import PantryService


@pytest.fixture
def pantry_client(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pantry.db'}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, sessions
    app.dependency_overrides.clear()
    engine.dispose()


def food_payload(name: str = "Rice") -> dict:
    return {
        "name": name,
        "source_type": "manual",
        "serving_quantity": 100,
        "serving_unit": "g",
        "grams_per_serving": 100,
        "calories_per_serving": 130,
        "protein_g": 3,
        "carbohydrate_g": 28,
        "fat_g": 1,
    }


def setup_household(client: TestClient) -> tuple[str, str, str]:
    household = client.post("/v1/households", json={"name": "Pantry home"}).json()["id"]
    refrigerator = client.post(
        f"/v1/households/{household}/pantry/locations",
        json={"name": "Refrigerator", "location_type": "refrigerator"},
    ).json()["id"]
    pantry = client.post(
        f"/v1/households/{household}/pantry/locations",
        json={"name": "Pantry", "location_type": "pantry"},
    ).json()["id"]
    return household, refrigerator, pantry


def today() -> date:
    return datetime.now(UTC).date()


def create_food(client: TestClient) -> str:
    response = client.post("/v1/foods", json=food_payload())
    assert response.status_code == 201
    return response.json()["id"]


def add_item(
    client: TestClient,
    household: str,
    location: str,
    food: str,
    quantity: int,
    expiration: date,
    unit: str = "g",
):
    response = client.post(
        f"/v1/households/{household}/pantry/items",
        json={
            "location_id": location,
            "food_id": food,
            "quantity": quantity,
            "unit": unit,
            "expiration_date": expiration.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_location_item_crud_and_household_isolation(pantry_client):
    client, _ = pantry_client
    household, refrigerator, pantry = setup_household(client)
    food = create_food(client)
    item = add_item(client, household, pantry, food, 100, today() + timedelta(days=5))
    other = client.post("/v1/households", json={"name": "Other"}).json()["id"]
    assert client.get(f"/v1/households/{other}/pantry/items").json() == []
    assert client.get(f"/v1/households/{other}/pantry/items/{item['id']}").status_code == 404
    assert client.put(
        f"/v1/households/{household}/pantry/items/{item['id']}",
        json={**item, "quantity": 90, "version": item["version"]},
    ).status_code == 200
    assert client.delete(f"/v1/households/{household}/pantry/locations/{pantry}").status_code == 409
    assert client.delete(f"/v1/households/{household}/pantry/locations/{refrigerator}").status_code == 204


def test_fefo_consumption_depletes_earliest_lot_and_keeps_audit(pantry_client):
    client, sessions = pantry_client
    household, _, pantry = setup_household(client)
    food = create_food(client)
    early = add_item(client, household, pantry, food, 100, today() + timedelta(days=1))
    late = add_item(client, household, pantry, food, 200, today() + timedelta(days=10))
    response = client.post(
        f"/v1/households/{household}/pantry/consume",
        json={"food_id": food, "quantity": 150, "unit": "g", "idempotency_key": "consume-1"},
    )
    assert response.status_code == 200
    items = {item["id"]: item for item in client.get(f"/v1/households/{household}/pantry/items").json()}
    assert items[early["id"]]["status"] == "depleted"
    assert Decimal(str(items[late["id"]]["quantity"])) == Decimal(150)
    with sessions() as session:
        transactions = session.scalars(select(PantryTransaction)).all()
        assert len(transactions) == 4
        assert all(transaction.created_at for transaction in transactions)


def test_expiration_low_stock_discard_and_duplicate_idempotency(pantry_client):
    client, _ = pantry_client
    household, _, pantry = setup_household(client)
    food = create_food(client)
    expired = add_item(client, household, pantry, food, 50, today() - timedelta(days=1))
    expiring = add_item(client, household, pantry, food, 100, today() + timedelta(days=2))
    assert len(client.get(f"/v1/households/{household}/pantry/expired").json()) == 1
    assert len(client.get(f"/v1/households/{household}/pantry/expiring").json()) == 1
    rule = client.put(
        f"/v1/households/{household}/pantry/stock-rules/{food}",
        json={
            "threshold_quantity": 200,
            "threshold_unit": "g",
            "preferred_reorder_quantity": 500,
            "preferred_reorder_unit": "g",
        },
    )
    assert rule.status_code == 200
    assert len(client.get(f"/v1/households/{household}/pantry/low-stock").json()) == 1
    discarded = client.post(
        f"/v1/households/{household}/pantry/items/{expiring['id']}/discard",
        json={"quantity_change": 100, "unit": "g", "version": expiring["version"], "idempotency_key": "discard-1"},
    )
    assert discarded.status_code == 200
    duplicate = client.post(
        f"/v1/households/{household}/pantry/items/{expired['id']}/discard",
        json={"quantity_change": 1, "unit": "g", "version": expired["version"], "idempotency_key": "discard-1"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "duplicate_idempotency_key"


def test_transfer_atomicity_and_stale_version(pantry_client):
    client, _ = pantry_client
    household, refrigerator, pantry = setup_household(client)
    food = create_food(client)
    item = add_item(client, household, refrigerator, food, 100, today() + timedelta(days=5))
    transferred = client.post(
        f"/v1/households/{household}/pantry/transfer",
        json={"item_id": item["id"], "target_location_id": pantry, "version": item["version"], "idempotency_key": "transfer-1"},
    )
    assert transferred.status_code == 200
    assert transferred.json()["location_id"] == pantry
    stale = client.post(
        f"/v1/households/{household}/pantry/transfer",
        json={"item_id": item["id"], "target_location_id": refrigerator, "version": item["version"], "idempotency_key": "transfer-2"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_inventory_version"


def test_incompatible_and_unsupported_units_and_manual_summary(pantry_client):
    client, _ = pantry_client
    household, _, pantry = setup_household(client)
    food = create_food(client)
    item = add_item(client, household, pantry, food, 100, today() + timedelta(days=5))
    incompatible = client.post(
        f"/v1/households/{household}/pantry/consume",
        json={"food_id": food, "quantity": 1, "unit": "cup"},
    )
    assert incompatible.status_code == 422
    unsupported = client.post(
        f"/v1/households/{household}/pantry/items/{item['id']}/adjust",
        json={"quantity_change": 1, "unit": "pinch", "version": item["version"]},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["code"] == "unsupported_conversion"
    summary = client.get(f"/v1/households/{household}/pantry/summary")
    assert summary.status_code == 200
    assert summary.json()["active_items"] == 1


def test_end_to_end_pantry_smoke_workflow(pantry_client):
    client, _ = pantry_client
    household, refrigerator, pantry = setup_household(client)
    food = create_food(client)
    early = add_item(client, household, refrigerator, food, 100, today() + timedelta(days=1))
    late = add_item(client, household, refrigerator, food, 200, today() + timedelta(days=2))

    consumed = client.post(
        f"/v1/households/{household}/pantry/consume",
        json={"food_id": food, "quantity": 150, "unit": "g", "idempotency_key": "smoke-consume"},
    )
    assert consumed.status_code == 200
    current_lots = {item["id"]: item for item in client.get(f"/v1/households/{household}/pantry/items").json()}
    assert current_lots[early["id"]]["status"] == "depleted"
    assert Decimal(str(current_lots[late["id"]]["quantity"])) == Decimal(150)

    transferred = client.post(
        f"/v1/households/{household}/pantry/transfer",
        json={
            "item_id": late["id"],
            "target_location_id": pantry,
            "version": current_lots[late["id"]]["version"],
            "idempotency_key": "smoke-transfer",
        },
    )
    assert transferred.status_code == 200
    assert transferred.json()["location_id"] == pantry
    assert len(client.get(f"/v1/households/{household}/pantry/expiring").json()) == 1

    rule = client.put(
        f"/v1/households/{household}/pantry/stock-rules/{food}",
        json={
            "threshold_quantity": 300,
            "threshold_unit": "g",
            "preferred_reorder_quantity": 500,
            "preferred_reorder_unit": "g",
        },
    )
    assert rule.status_code == 200
    low_stock = client.get(f"/v1/households/{household}/pantry/low-stock")
    assert low_stock.status_code == 200
    assert len(low_stock.json()) == 1


def test_two_sessions_stale_pantry_update_fails_atomically(pantry_client):
    client, sessions = pantry_client
    household, _, pantry = setup_household(client)
    food = create_food(client)
    item = add_item(client, household, pantry, food, 100, today() + timedelta(days=5))
    first = sessions()
    second = sessions()
    try:
        first_item = first.get(PantryItem, UUID(item["id"]))
        second_item = second.get(PantryItem, UUID(item["id"]))
        first_item.quantity = Decimal(90)
        first.commit()
        second_item.quantity = Decimal(80)
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()
        with sessions() as verification:
            current = verification.get(PantryItem, UUID(item["id"]))
            assert current.quantity == Decimal(90)
            assert current.version == 2
    finally:
        first.close()
        second.close()


def test_transfer_rolls_back_source_destination_and_both_audits(pantry_client, monkeypatch):
    client, sessions = pantry_client
    household, refrigerator, pantry = setup_household(client)
    food = create_food(client)
    item = add_item(client, household, refrigerator, food, 100, today() + timedelta(days=5))
    with sessions() as session:
        service = PantryService(session)
        original = service._transaction
        calls = 0

        def fail_after_source(*args, **kwargs):
            nonlocal calls
            calls += 1
            original(*args, **kwargs)
            if calls == 2:
                raise RuntimeError("forced transfer failure")

        monkeypatch.setattr(service, "_transaction", fail_after_source)
        with pytest.raises(RuntimeError, match="forced transfer failure"):
            service.transfer(
                UUID(household),
                PantryTransferRequest(
                    item_id=UUID(item["id"]),
                    target_location_id=UUID(pantry),
                    version=item["version"],
                    idempotency_key="rollback-transfer",
                ),
            )
        session.rollback()
        assert session.query(PantryTransaction).count() == 1
        source = session.get(PantryItem, UUID(item["id"]))
        assert source.quantity == Decimal(100)
        assert source.location_id == UUID(refrigerator)
        assert session.query(PantryItem).count() == 1


def test_repeated_fractional_consumption_has_no_float_drift(pantry_client):
    client, _ = pantry_client
    household, _, pantry = setup_household(client)
    food = create_food(client)
    item = add_item(client, household, pantry, food, 1, today() + timedelta(days=5), unit="kg")
    for index in range(10):
        response = client.post(
            f"/v1/households/{household}/pantry/consume",
            json={"food_id": food, "quantity": "0.1", "unit": "kg", "idempotency_key": f"fraction-{index}"},
        )
        assert response.status_code == 200
    current = client.get(f"/v1/households/{household}/pantry/items/{item['id']}").json()
    assert Decimal(str(current["quantity"])) == Decimal(0)
    assert current["status"] == "depleted"


def test_expired_lot_cannot_be_consumed(pantry_client):
    client, _ = pantry_client
    household, _, pantry = setup_household(client)
    food = create_food(client)
    add_item(client, household, pantry, food, 100, today() - timedelta(days=1))
    response = client.post(
        f"/v1/households/{household}/pantry/consume",
        json={"food_id": food, "quantity": 1, "unit": "g"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "expired_inventory"


def test_all_mutating_operations_reject_repeated_idempotency_keys(pantry_client):
    client, _ = pantry_client
    household, refrigerator, pantry = setup_household(client)
    food = create_food(client)
    item = add_item(client, household, pantry, food, 100, today() + timedelta(days=5))
    consume_payload = {"food_id": food, "quantity": 10, "unit": "g", "idempotency_key": "consume-once"}
    assert client.post(f"/v1/households/{household}/pantry/consume", json=consume_payload).status_code == 200
    assert client.post(f"/v1/households/{household}/pantry/consume", json=consume_payload).json()["code"] == "duplicate_idempotency_key"

    current = client.get(f"/v1/households/{household}/pantry/items/{item['id']}").json()
    adjust_payload = {"quantity_change": 5, "unit": "g", "version": current["version"], "idempotency_key": "adjust-once"}
    assert client.post(f"/v1/households/{household}/pantry/items/{item['id']}/adjust", json=adjust_payload).status_code == 200
    assert client.post(f"/v1/households/{household}/pantry/items/{item['id']}/adjust", json=adjust_payload).json()["code"] == "duplicate_idempotency_key"

    current = client.get(f"/v1/households/{household}/pantry/items/{item['id']}").json()
    discard_payload = {"quantity_change": 5, "unit": "g", "version": current["version"], "idempotency_key": "discard-once"}
    assert client.post(f"/v1/households/{household}/pantry/items/{item['id']}/discard", json=discard_payload).status_code == 200
    assert client.post(f"/v1/households/{household}/pantry/items/{item['id']}/discard", json=discard_payload).json()["code"] == "duplicate_idempotency_key"

    transfer_item = add_item(client, household, refrigerator, food, 20, today() + timedelta(days=5))
    transfer_payload = {
        "item_id": transfer_item["id"],
        "target_location_id": pantry,
        "version": transfer_item["version"],
        "idempotency_key": "transfer-once",
    }
    assert client.post(f"/v1/households/{household}/pantry/transfer", json=transfer_payload).status_code == 200
    assert client.post(f"/v1/households/{household}/pantry/transfer", json=transfer_payload).json()["code"] == "duplicate_idempotency_key"


def test_database_idempotency_conflict_maps_to_duplicate_error(pantry_client, monkeypatch):
    client, sessions = pantry_client
    household, _, pantry = setup_household(client)
    food = create_food(client)
    item = add_item(client, household, pantry, food, 100, today() + timedelta(days=5))
    with sessions() as session:
        session.add(
            PantryTransaction(
                household_id=UUID(household),
                pantry_item_id=UUID(item["id"]),
                transaction_type=PantryTransactionType.ADJUST,
                quantity_change=Decimal(1),
                unit="g",
                idempotency_key="database-race",
            )
        )
        session.commit()
        service = PantryService(session)
        monkeypatch.setattr(service, "_idempotency", lambda *args: None)
        with pytest.raises(Exception, match="already been used"):
            service.adjust(
                UUID(household),
                UUID(item["id"]),
                PantryAdjustment(
                    quantity_change=Decimal(1),
                    unit="g",
                    idempotency_key="database-race",
                    version=item["version"],
                ),
            )