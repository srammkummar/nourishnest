import logging
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from nourish_nest.config import Settings, get_settings
from nourish_nest.provider_cache import Cache, InMemoryTTLCache

logger = logging.getLogger(__name__)


class ProviderNotConfiguredError(RuntimeError):
    code = "provider_not_configured"


class ProviderUnavailableError(RuntimeError):
    code = "provider_unavailable"


class ProviderRateLimitedError(RuntimeError):
    code = "provider_rate_limited"


class ExternalFoodNotFoundError(LookupError):
    code = "external_food_not_found"


class InvalidProviderResponseError(RuntimeError):
    code = "invalid_provider_response"


class ProviderFoodSummary(BaseModel):
    fdc_id: int = Field(validation_alias="fdcId")
    description: str
    data_type: str | None = None
    brand_owner: str | None = None
    brand_name: str | None = None

    model_config = {"populate_by_name": True}


class ProviderSearchResponse(BaseModel):
    foods: list[ProviderFoodSummary] = Field(default_factory=list)
    total_hits: int | None = Field(default=None, validation_alias="totalHits")

    model_config = {"populate_by_name": True}


class ProviderFood(BaseModel):
    fdc_id: int
    description: str
    data_type: str | None = None
    brand_owner: str | None = None
    brand_name: str | None = None
    serving_quantity: Decimal = Decimal(100)
    serving_unit: str = "g"
    household_serving_text: str | None = None
    grams_per_serving: Decimal | None = Decimal(100)
    calories_per_serving: Decimal | None = None
    protein_g: Decimal | None = None
    carbohydrate_g: Decimal | None = None
    fat_g: Decimal | None = None
    fiber_g: Decimal | None = None
    sugar_g: Decimal | None = None
    sodium_mg: Decimal | None = None
    retrieved_at: datetime
    source_attribution: str = "USDA FoodData Central"


class FoodDataProvider(Protocol):
    def search(self, query: str, page_size: int = 25) -> ProviderSearchResponse: ...
    def get_food(self, fdc_id: int) -> ProviderFood: ...


NUTRIENT_NAMES = {
    "208": "calories_per_serving",
    "1008": "calories_per_serving",
    "203": "protein_g",
    "1003": "protein_g",
    "205": "carbohydrate_g",
    "1005": "carbohydrate_g",
    "204": "fat_g",
    "1004": "fat_g",
    "291": "fiber_g",
    "1079": "fiber_g",
    "269": "sugar_g",
    "2000": "sugar_g",
    "307": "sodium_mg",
    "1093": "sodium_mg",
}


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _nutrient_value(nutrient: dict[str, Any]) -> Decimal | None:
    return _decimal(nutrient.get("amount", nutrient.get("value")))


def normalize_usda_food(payload: dict[str, Any]) -> ProviderFood:
    try:
        fdc_id = int(payload["fdcId"])
        description = str(payload["description"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidProviderResponseError("USDA food response is missing required fields") from exc
    serving_quantity = _decimal(payload.get("servingSize")) or Decimal(100)
    serving_unit = str(payload.get("servingSizeUnit") or "g").lower()
    grams = serving_quantity if serving_unit in {"g", "gram", "grams"} else None
    values: dict[str, Decimal | None] = {field: None for field in NUTRIENT_NAMES.values()}
    for nutrient in payload.get("foodNutrients", []):
        number = str(nutrient.get("nutrientNumber", nutrient.get("nutrientId", "")))
        field = NUTRIENT_NAMES.get(number)
        if field:
            values[field] = _nutrient_value(nutrient)
    try:
        return ProviderFood(
            fdc_id=fdc_id,
            description=description,
            data_type=payload.get("dataType"),
            brand_owner=payload.get("brandOwner"),
            brand_name=payload.get("brandName"),
            serving_quantity=serving_quantity,
            serving_unit=serving_unit,
            household_serving_text=payload.get("householdServingFullText"),
            grams_per_serving=grams,
            **values,
            retrieved_at=datetime.now(UTC),
        )
    except ValidationError as exc:
        raise InvalidProviderResponseError("USDA food response failed validation") from exc


class USDAFoodDataProvider:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        cache: Cache[ProviderSearchResponse | ProviderFood] | None = None,
    ):
        self.settings = settings or get_settings()
        self.client = client or httpx.Client(
            base_url=self.settings.usda_base_url.rstrip("/"),
            timeout=httpx.Timeout(self.settings.usda_timeout_seconds),
        )
        self.cache = cache or InMemoryTTLCache()

    def _request(self, path: str, params: dict[str, Any]) -> Any:
        if not self.settings.usda_api_key:
            raise ProviderNotConfiguredError("USDA API key is not configured")
        request_params = {**params, "api_key": self.settings.usda_api_key}
        for attempt in range(self.settings.usda_max_retries + 1):
            try:
                response = self.client.get(path, params=request_params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self.settings.usda_max_retries:
                    raise ProviderUnavailableError("USDA provider is unavailable") from exc
                self._backoff(attempt)
                continue
            if response.status_code == 429:
                raise ProviderRateLimitedError("USDA provider rate limit reached")
            if 400 <= response.status_code < 500:
                if response.status_code == 404:
                    raise ExternalFoodNotFoundError("USDA food was not found")
                raise ProviderUnavailableError("USDA provider rejected the request")
            if response.status_code >= 500:
                if attempt >= self.settings.usda_max_retries:
                    raise ProviderUnavailableError("USDA provider is unavailable")
                self._backoff(attempt)
                continue
            try:
                return response.json()
            except ValueError as exc:
                raise InvalidProviderResponseError("USDA provider returned invalid JSON") from exc
        raise ProviderUnavailableError("USDA provider is unavailable")

    def _backoff(self, attempt: int) -> None:
        logger.info("Retrying USDA request", extra={"attempt": attempt + 1})
        time.sleep(2**attempt)

    def search(self, query: str, page_size: int = 25) -> ProviderSearchResponse:
        key = f"usda:search:{query.strip().casefold()}:{page_size}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        payload = self._request("/foods/search", {"query": query, "pageSize": page_size})
        if not isinstance(payload, dict) or "foods" not in payload:
            raise InvalidProviderResponseError("USDA search response failed validation")
        try:
            result = ProviderSearchResponse.model_validate(payload)
        except ValidationError as exc:
            raise InvalidProviderResponseError("USDA search response failed validation") from exc
        self.cache.set(key, result, self.settings.usda_cache_ttl_seconds)
        return result

    def get_food(self, fdc_id: int) -> ProviderFood:
        key = f"usda:food:{fdc_id}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        result = normalize_usda_food(self._request(f"/food/{fdc_id}", {}))
        self.cache.set(key, result, self.settings.usda_cache_ttl_seconds)
        return result


def get_food_data_provider(settings: Settings | None = None) -> FoodDataProvider:
    selected = (settings or get_settings()).food_data_provider.casefold()
    if selected == "usda":
        return USDAFoodDataProvider(settings)
    if selected == "fake":
        return FakeFoodDataProvider()
    raise ProviderNotConfiguredError("Food data provider is not configured")

class FakeFoodDataProvider:
    def __init__(self, foods: list[ProviderFood] | None = None):
        self.foods = {food.fdc_id: food for food in foods or []}
        self.search_calls = 0
        self.detail_calls = 0

    def search(self, query: str, page_size: int = 25) -> ProviderSearchResponse:
        self.search_calls += 1
        needle = query.casefold()
        matches = [
            ProviderFoodSummary(
                fdc_id=food.fdc_id,
                description=food.description,
                data_type=food.data_type,
                brand_owner=food.brand_owner,
                brand_name=food.brand_name,
            )
            for food in self.foods.values()
            if needle in food.description.casefold()
        ][:page_size]
        return ProviderSearchResponse(foods=matches, total_hits=len(matches))

    def get_food(self, fdc_id: int) -> ProviderFood:
        self.detail_calls += 1
        try:
            return self.foods[fdc_id]
        except KeyError as exc:
            raise ExternalFoodNotFoundError("USDA food was not found") from exc