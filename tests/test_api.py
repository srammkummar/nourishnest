from fastapi.testclient import TestClient

from nourish_nest.api import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"]


def test_nutrition_endpoint():
    response = client.post(
        "/v1/nutrition/calculate",
        json={
            "age": 40,
            "sex": "male",
            "height_cm": 180,
            "weight_kg": 85,
            "activity_level": "moderate",
            "goal": "maintain",
            "weekly_goal_kg": 0,
            "meals_per_day": 4,
        },
    )
    assert response.status_code == 200
    assert len(response.json()["meals"]) == 4


def test_validation_errors_have_stable_contract_and_request_id():
    response = client.post(
        "/v1/nutrition/calculate",
        headers={"x-request-id": "test-request-123"},
        json={"age": 12},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["request_id"] == "test-request-123"


def test_minor_returns_supported_error_contract():
    response = client.post(
        "/v1/nutrition/calculate",
        json={
            "age": 17,
            "sex": "female",
            "height_cm": 165,
            "weight_kg": 65,
            "activity_level": "moderate",
            "goal": "maintain",
            "weekly_goal_kg": 0,
            "meals_per_day": 3,
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_profile"
    assert response.json()["request_id"]

