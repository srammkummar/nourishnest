from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from nourish_nest.api import app
from nourish_nest.config import Settings
from nourish_nest.database import Base, get_db
from nourish_nest.models import Food
from nourish_nest.providers import (
    FakeFoodDataProvider,
    InvalidProviderResponseError,
    ProviderFood,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    USDAFoodDataProvider,
)


def provider_food(fdc_id: int = 123, calories: str = "130") -> ProviderFood:
    return ProviderFood(
        fdc_id=fdc_id,
        description="Brown rice, cooked",
        data_type="SR Legacy",
        brand_owner="Example Foods",
        brand_name="Example",
        serving_quantity=Decimal(100),
        serving_unit="g",
        grams_per_serving=Decimal(100),
        calories_per_serving=Decimal(calories),
        protein_g=Decimal(3),
        carbohydrate_g=Decimal(28),
        fat_g=Decimal(1),
        fiber_g=None,
        sugar_g=Decimal(1),
        sodium_mg=None,
        retrieved_at=datetime.now(UTC),
    )


def settings(**overrides) -> Settings:
    values = {
        "food_data_provider": "usda",
        "usda_api_key": "test-key-secret",
        "usda_base_url": "https://example.test/fdc/v1",
        "usda_timeout_seconds": 1,
        "usda_max_retries": 2,
        "usda_cache_ttl_seconds": 300,
    }
    values.update(overrides)
    return Settings(**values)


def client_for(handler, **setting_overrides):
    transport = httpx.MockTransport(handler)
    return USDAFoodDataProvider(settings=settings(**setting_overrides), client=httpx.Client(transport=transport, base_url="https://example.test/fdc/v1"))


def test_usda_search_detail_mapping_and_missing_nutrients():
    def handler(request: httpx.Request):
        assert request.url.params["api_key"] == "test-key-secret"
        if request.url.path.endswith("/foods/search"):
            return httpx.Response(200, json={"foods": [{"fdcId": 123, "description": "Brown rice"}], "totalHits": 1})
        return httpx.Response(200, json={
            "fdcId": 123,
            "description": "Brown rice, cooked",
            "dataType": "SR Legacy",
            "brandOwner": "Example Foods",
            "foodNutrients": [
                {"nutrientNumber": "208", "value": 130},
                {"nutrientNumber": "203", "value": 3},
                {"nutrientNumber": "205", "value": 28},
                {"nutrientNumber": "204", "value": 1},
            ],
            "servingSize": 100,
            "servingSizeUnit": "g",
        })

    provider = client_for(handler)
    search = provider.search("brown rice")
    food = provider.get_food(123)
    assert search.foods[0].fdc_id == 123
    assert food.calories_per_serving == Decimal(130)
    assert food.fiber_g is None
    assert food.brand_owner == "Example Foods"
    assert food.source_attribution == "USDA FoodData Central"


def test_empty_search_results_and_cache_hits():
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"foods": [], "totalHits": 0})

    provider = client_for(handler)
    assert provider.search("nothing").foods == []
    assert provider.search("nothing").foods == []
    assert calls == 1


def test_cache_expiration(monkeypatch):
    now = 0.0
    monkeypatch.setattr("nourish_nest.provider_cache.monotonic", lambda: now)
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"foods": [], "totalHits": 0})

    provider = client_for(handler, usda_cache_ttl_seconds=5)
    provider.search("rice")
    provider.search("rice")
    now = 6.0
    provider.search("rice")
    assert calls == 2


def test_timeout_retries_then_unavailable(monkeypatch):
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("nourish_nest.providers.time.sleep", lambda seconds: None)
    provider = client_for(handler, usda_max_retries=2)
    with pytest.raises(ProviderUnavailableError):
        provider.search("rice")
    assert calls == 3


def test_rate_limit_does_not_retry():
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": "rate limited"})

    provider = client_for(handler)
    with pytest.raises(ProviderRateLimitedError):
        provider.search("rice")
    assert calls == 1


def test_invalid_provider_response():
    provider = client_for(lambda request: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(InvalidProviderResponseError):
        provider.search("rice")


@pytest.fixture
def provider_api(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'provider.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    fake = FakeFoodDataProvider([provider_food()])

    def override_db():
        with sessions() as session:
            yield session

    def override_provider():
        return fake

    app.dependency_overrides[get_db] = override_db
    from nourish_nest.providers import get_food_data_provider
    app.dependency_overrides[get_food_data_provider] = override_provider
    with TestClient(app) as client:
        yield client, sessions, fake
    app.dependency_overrides.clear()
    engine.dispose()


def test_provider_api_search_detail_import_duplicate_and_refresh(provider_api):
    client, sessions, fake = provider_api
    search = client.get("/v1/providers/usda/search", params={"q": "rice"})
    assert search.status_code == 200
    assert search.json()["foods"][0]["fdc_id"] == 123
    detail = client.get("/v1/providers/usda/foods/123")
    assert detail.status_code == 200
    imported = client.post("/v1/foods/import/usda/123")
    assert imported.status_code == 201, imported.text
    food_id = imported.json()["id"]
    assert imported.json()["external_source_identifier"] == "123"
    assert imported.json()["source_provider"] == "usda_fdc"
    assert imported.json()["source_attribution"] == "USDA FoodData Central"
    other_provider = client.post(
        "/v1/foods",
        json={
            "name": "Other provider food",
            "source_type": "external",
            "source_provider": "open_food_facts",
            "external_source_identifier": "123",
            "serving_quantity": 100,
            "serving_unit": "g",
        },
    )
    assert other_provider.status_code == 201, other_provider.text
    duplicate = client.post("/v1/foods/import/usda/123", headers={"x-request-id": "duplicate-1"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "conflict"
    fake.foods[123] = provider_food(calories="150")
    refreshed = client.post(f"/v1/foods/{food_id}/refresh/usda")
    assert refreshed.status_code == 200
    assert refreshed.json()["id"] == food_id
    assert Decimal(str(refreshed.json()["calories_per_serving"])) == Decimal(150)
    with sessions() as session:
        assert session.query(Food).count() == 2


def test_manual_foods_do_not_require_provider(provider_api):
    client, _, _ = provider_api
    manual = client.post(
        "/v1/foods",
        json={"name": "Manual food", "serving_quantity": 1, "serving_unit": "item"},
    )
    assert manual.status_code == 201
    assert manual.json()["source_type"] == "manual"
    assert manual.json()["source_provider"] is None


def test_external_foods_require_provider_and_identifier(provider_api):
    client, _, _ = provider_api
    response = client.post(
        "/v1/foods",
        json={"name": "Invalid external", "source_type": "external", "serving_quantity": 1, "serving_unit": "item"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_provider_api_key_is_not_returned(provider_api):
    client, _, _ = provider_api
    response = client.get("/v1/providers/usda/search", params={"q": "rice"})
    assert "test-key-secret" not in response.text