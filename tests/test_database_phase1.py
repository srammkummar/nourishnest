from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from nourish_nest.api import app
from nourish_nest.config import get_settings
from nourish_nest.database import Base, get_db


@pytest.fixture
def client(tmp_path: Path):
    database_path = tmp_path / "test.db"
    test_engine = create_engine(
        f"sqlite:///{database_path}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(test_engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(test_engine)
    test_session = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_db():
        with test_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    test_engine.dispose()


def member_payload(name: str = "Alex") -> dict:
    return {
        "name": name,
        "age": 35,
        "sex": "female",
        "height_cm": 165,
        "weight_kg": 68,
        "activity_level": "moderate",
        "goal": "lose",
        "weekly_goal_kg": 0.25,
        "meals_per_day": 3,
        "dietary_preferences": [
            {"preference_type": "vegetarian", "value": "vegetarian"},
            {"preference_type": "custom", "value": "low sodium"},
        ],
        "allergies": [{"allergen": "peanuts", "severity": "severe", "notes": "EpiPen"}],
    }


def create_household(client: TestClient, name: str = "Home") -> dict:
    response = client.post("/v1/households", json={"name": name})
    assert response.status_code == 201
    return response.json()


def test_household_and_member_lifecycle_cascades_dependents(client: TestClient):
    household = create_household(client)
    household_id = household["id"]
    member = client.post(f"/v1/households/{household_id}/members", json=member_payload())
    assert member.status_code == 201
    member_id = member.json()["id"]
    assert UUID(member_id)

    assert client.get(f"/v1/households/{household_id}").status_code == 200
    assert len(client.get(f"/v1/households/{household_id}/members").json()) == 1
    assert client.delete(f"/v1/households/{household_id}").status_code == 204
    assert client.get(f"/v1/members/{member_id}").status_code == 404


def test_relationship_isolation_and_member_update(client: TestClient):
    first = create_household(client, "First")
    second = create_household(client, "Second")
    member_response = client.post(
        f"/v1/households/{first['id']}/members", json=member_payload("First member")
    )
    member_id = member_response.json()["id"]

    assert len(client.get(f"/v1/households/{first['id']}/members").json()) == 1
    assert client.get(f"/v1/households/{second['id']}/members").json() == []
    updated = member_payload("Updated member")
    updated["goal"] = "maintain"
    updated["weekly_goal_kg"] = 0.5
    response = client.put(f"/v1/members/{member_id}", json=updated)
    assert response.status_code == 200
    assert response.json()["name"] == "Updated member"
    assert response.json()["weekly_goal_kg"] == 0


def test_saved_member_preferences_allergies_and_nutrition(client: TestClient):
    household = create_household(client)
    response = client.post(f"/v1/households/{household['id']}/members", json=member_payload())
    member = response.json()
    assert member["dietary_preferences"][0]["preference_type"] == "vegetarian"
    assert member["allergies"][0]["severity"] == "severe"

    nutrition = client.post(f"/v1/members/{member['id']}/nutrition/calculate")
    assert nutrition.status_code == 200
    assert nutrition.json()["target_calories"] == 1857


def test_missing_and_invalid_records_keep_error_contract(client: TestClient):
    missing_id = "00000000-0000-0000-0000-000000000001"
    response = client.get(f"/v1/households/{missing_id}", headers={"x-request-id": "missing-1"})
    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "Household not found",
        "request_id": "missing-1",
    }

    invalid = member_payload()
    invalid["weekly_goal_kg"] = 0
    response = client.post(f"/v1/households/{missing_id}/members", json=invalid)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["request_id"]


def test_database_tests_use_temporary_database(client: TestClient):
    assert Path(get_settings().database_url.removeprefix("sqlite:///")) != Path("test.db")