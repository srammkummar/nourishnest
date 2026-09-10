"""HTTP-only UI boundary. Keep these wire models independent of server ORM schemas."""

import time
import uuid
from collections.abc import Callable
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

import nourish_nest.grocery_client_models as grocery
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
from nourish_nest.recipe_client_models import (
    RecipeInput,
    RecipeNutrition,
    RecipeRecord,
    StoredFood,
    StoredFoodSearch,
)

T = TypeVar("T")


class UISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")
    api_base_url: str = "http://127.0.0.1:8000"


class APIError(RuntimeError):
    def __init__(
        self, code: str, message: str, request_id: str | None = None, status_code: int | None = None
    ):
        self.code = code
        self.message = message
        self.request_id = request_id
        self.status_code = status_code
        super().__init__(message)


class APIUnavailableError(APIError):
    pass


class APITimeoutError(APIError):
    pass


class APIProtocolError(APIError):
    pass


class APIResponseError(APIError):
    pass


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    request_id: str


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class Household(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str = "UTC"
    currency: str = "USD"


class Record(BaseModel):
    id: uuid.UUID


class Preference(BaseModel):
    preference_type: Literal[
        "vegetarian", "vegan", "pescatarian", "halal", "no-beef", "no-pork", "custom"
    ]
    value: str = Field(min_length=1, max_length=200)


class Allergy(BaseModel):
    allergen: str = Field(min_length=1, max_length=200)
    severity: Literal["mild", "moderate", "severe"]
    notes: str | None = Field(default=None, max_length=2000)


class MemberInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    age: int = Field(ge=13, le=100)
    sex: Literal["female", "male"]
    height_cm: float = Field(gt=100, le=250, allow_inf_nan=False)
    weight_kg: float = Field(gt=30, le=350, allow_inf_nan=False)
    activity_level: Literal["sedentary", "light", "moderate", "very_active"]
    goal: Literal["lose", "maintain", "gain"]
    weekly_goal_kg: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)
    meals_per_day: int = Field(default=3, ge=2, le=6)
    dietary_preferences: list[Preference] = Field(default_factory=list)
    allergies: list[Allergy] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_profile(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Enter a member name.")
        if self.goal == "maintain":
            self.weekly_goal_kg = 0
        elif self.weekly_goal_kg == 0:
            raise ValueError("Weekly change must be greater than zero for loss or gain.")
        for preference in self.dietary_preferences:
            if not preference.value.strip():
                raise ValueError("Dietary preference values cannot be blank.")
        for allergy in self.allergies:
            if not allergy.allergen.strip():
                raise ValueError("Allergen names cannot be blank.")
        return self


class Member(MemberInput):
    id: uuid.UUID
    household_id: uuid.UUID
    version: int = Field(ge=1)


class NutritionMacros(BaseModel):
    protein_g: int
    carbohydrate_g: int
    fat_g: int


class MemberNutrition(BaseModel):
    bmr_calories: int
    maintenance_calories: int
    target_calories: int
    macros: NutritionMacros
    calculation_version: str | None = None
    warnings: list[str] = Field(default_factory=list)


class GroceryListRecord(Record):
    status: Literal["draft", "active", "completed", "archived"]


class PantrySummary(BaseModel):
    active_items: int = Field(ge=0)
    low_stock_food_ids: list[uuid.UUID]


class DashboardCounts(BaseModel):
    members: int
    recipes: int
    active_pantry_items: int
    expiring_items: int
    low_stock_items: int
    active_grocery_lists: int


class APIClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        try:
            url = httpx.URL(base_url)
            valid = (
                url.scheme in {"http", "https"}
                and url.host
                and not (url.userinfo or url.query or url.fragment)
            )
        except httpx.InvalidURL:
            valid = False
        if not valid:
            raise APIProtocolError(
                "invalid_api_url",
                "APP_API_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment.",
            )
        self.base_url = str(url).rstrip("/")
        self.timeout = httpx.Timeout(connect=2.0, read=8.0, write=8.0, pool=2.0)
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(follow_redirects=False)
        self._sleep = sleep

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        adapter: TypeAdapter[T],
        *,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
        expected_status: int = 200,
        retry_safe: bool = True,
    ) -> T:
        request_id = str(uuid.uuid4())
        attempts = 2 if method == "GET" and retry_safe else 1
        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=body,
                    headers={**(headers or {}), "x-request-id": request_id},
                    timeout=self.timeout,
                )
            except httpx.TimeoutException:
                if attempt + 1 < attempts:
                    self._sleep(0.15)
                    continue
                raise APITimeoutError(
                    "api_timeout", "The API did not respond in time. Try refreshing.", request_id
                ) from None
            except httpx.RequestError:
                if attempt + 1 < attempts:
                    self._sleep(0.15)
                    continue
                raise APIUnavailableError(
                    "api_unavailable",
                    "Could not connect to the API. Check that FastAPI is running.",
                    request_id,
                ) from None
            if response.status_code in {502, 503, 504} and attempt + 1 < attempts:
                self._sleep(0.15)
                continue
            response_id = response.headers.get("x-request-id", request_id)
            if response.status_code != expected_status:
                if response.is_error:
                    try:
                        error = ErrorEnvelope.model_validate(response.json())
                    except (ValueError, ValidationError):
                        raise APIProtocolError(
                            "invalid_response",
                            "The API returned an unreadable error response.",
                            response_id,
                            response.status_code,
                        ) from None
                    raise APIResponseError(
                        error.code, error.message, error.request_id, response.status_code
                    )
                raise APIProtocolError(
                    "unexpected_status",
                    "The API returned an unexpected HTTP status.",
                    response_id,
                    response.status_code,
                )
            try:
                return adapter.validate_python(None if expected_status == 204 else response.json())
            except (ValueError, ValidationError):
                raise APIProtocolError(
                    "invalid_response",
                    "The API returned invalid JSON or unexpected data.",
                    response_id,
                    response.status_code,
                ) from None
        raise AssertionError("Unreachable request state")

    def health(self) -> Health:
        return self._request("GET", "/health", TypeAdapter(Health))

    def households(self) -> list[Household]:
        return self._request("GET", "/v1/households", TypeAdapter(list[Household]))

    def create_household(
        self, name: str, timezone: str = "UTC", currency: str = "USD"
    ) -> Household:
        return self._request(
            "POST",
            "/v1/households",
            TypeAdapter(Household),
            expected_status=201,
            body={
                "name": name.strip(),
                "timezone": timezone.strip(),
                "currency": currency.strip().upper(),
            },
        )

    def dashboard(self, household_id: uuid.UUID) -> DashboardCounts:
        base = f"/v1/households/{household_id}"
        members = self._request("GET", f"{base}/members", TypeAdapter(list[Record]))
        recipes = self._request("GET", f"{base}/recipes", TypeAdapter(list[Record]))
        # Legacy pantry GETs mark expired lots. They must not be automatically retried.
        pantry = self._request(
            "GET", f"{base}/pantry/summary", TypeAdapter(PantrySummary), retry_safe=False
        )
        expiring = self._request(
            "GET", f"{base}/pantry/expiring", TypeAdapter(list[Record]), retry_safe=False
        )
        lists = self._request("GET", f"{base}/grocery-lists", TypeAdapter(list[GroceryListRecord]))
        return DashboardCounts(
            members=len(members),
            recipes=len(recipes),
            active_pantry_items=pantry.active_items,
            expiring_items=len(expiring),
            low_stock_items=len(pantry.low_stock_food_ids),
            active_grocery_lists=sum(row.status == "active" for row in lists),
        )

    def members(self, household_id: uuid.UUID) -> list[Member]:
        return self._request(
            "GET", f"/v1/households/{household_id}/members", TypeAdapter(list[Member])
        )

    def create_member(self, household_id: uuid.UUID, member: MemberInput) -> Member:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/members",
            TypeAdapter(Member),
            body=member.model_dump(mode="json"),
            expected_status=201,
        )

    def get_member(self, household_id: uuid.UUID, member_id: uuid.UUID) -> Member:
        return self._request(
            "GET", f"/v1/households/{household_id}/members/{member_id}", TypeAdapter(Member)
        )

    def update_member(
        self,
        household_id: uuid.UUID,
        member_id: uuid.UUID,
        member: MemberInput,
        expected_version: int,
    ) -> Member:
        return self._request(
            "PUT",
            f"/v1/households/{household_id}/members/{member_id}",
            TypeAdapter(Member),
            body={**member.model_dump(mode="json"), "expected_version": expected_version},
        )

    def delete_member(
        self, household_id: uuid.UUID, member_id: uuid.UUID, expected_version: int
    ) -> None:
        self._request(
            "DELETE",
            f"/v1/households/{household_id}/members/{member_id}?expected_version={expected_version}",
            TypeAdapter(type(None)),
            expected_status=204,
        )

    def member_nutrition(self, household_id: uuid.UUID, member_id: uuid.UUID) -> MemberNutrition:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/members/{member_id}/nutrition/calculate",
            TypeAdapter(MemberNutrition),
        )

    def search_foods(self, query: str) -> list[StoredFood]:
        query_string = str(httpx.QueryParams({"q": query}))
        return self._request(
            "GET", f"/v1/foods/search?{query_string}", TypeAdapter(StoredFoodSearch)
        ).foods

    def recipes(self, household_id: uuid.UUID) -> list[RecipeRecord]:
        return self._request(
            "GET", f"/v1/households/{household_id}/recipes", TypeAdapter(list[RecipeRecord])
        )

    def grocery_lists(self, household_id: uuid.UUID) -> list[grocery.GroceryList]:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/grocery-lists",
            TypeAdapter(list[grocery.GroceryList]),
        )

    def grocery_list(self, household_id: uuid.UUID, list_id: uuid.UUID) -> grocery.GroceryList:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/grocery-lists/{list_id}",
            TypeAdapter(grocery.GroceryList),
        )

    def create_grocery_list(
        self, household_id: uuid.UUID, data: grocery.ListInput
    ) -> grocery.GroceryList:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/grocery-lists",
            TypeAdapter(grocery.GroceryList),
            body=data.model_dump(mode="json"),
            expected_status=201,
        )

    def update_grocery_list(
        self, household_id: uuid.UUID, list_id: uuid.UUID, data: grocery.ListUpdate
    ) -> grocery.GroceryList:
        return self._request(
            "PUT",
            f"/v1/households/{household_id}/grocery-lists/{list_id}",
            TypeAdapter(grocery.GroceryList),
            body=data.model_dump(mode="json"),
        )

    def delete_grocery_list(
        self, household_id: uuid.UUID, list_id: uuid.UUID, expected_version: int
    ) -> None:
        self._request(
            "DELETE",
            f"/v1/households/{household_id}/grocery-lists/{list_id}?expected_version={expected_version}",
            TypeAdapter(type(None)),
            expected_status=204,
        )

    def grocery_items(
        self, household_id: uuid.UUID, list_id: uuid.UUID
    ) -> list[grocery.GroceryItem]:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/grocery-lists/{list_id}/items",
            TypeAdapter(list[grocery.GroceryItem]),
        )

    def grocery_item(
        self, household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID
    ) -> grocery.GroceryItem:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/grocery-lists/{list_id}/items/{item_id}",
            TypeAdapter(grocery.GroceryItem),
        )

    def create_grocery_item(
        self, household_id: uuid.UUID, list_id: uuid.UUID, data: grocery.ItemInput
    ) -> grocery.GroceryItem:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/grocery-lists/{list_id}/items",
            TypeAdapter(grocery.GroceryItem),
            body=data.model_dump(mode="json"),
            expected_status=201,
        )

    def update_grocery_item(
        self,
        household_id: uuid.UUID,
        list_id: uuid.UUID,
        item_id: uuid.UUID,
        data: grocery.ItemUpdate,
    ) -> grocery.GroceryItem:
        return self._request(
            "PUT",
            f"/v1/households/{household_id}/grocery-lists/{list_id}/items/{item_id}",
            TypeAdapter(grocery.GroceryItem),
            body=data.model_dump(mode="json"),
        )

    def delete_grocery_item(
        self, household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID, expected_version: int
    ) -> None:
        self._request(
            "DELETE",
            f"/v1/households/{household_id}/grocery-lists/{list_id}/items/{item_id}?expected_version={expected_version}",
            TypeAdapter(type(None)),
            expected_status=204,
        )

    def grocery_requirements(
        self, household_id: uuid.UUID, data: grocery.RequirementsInput
    ) -> grocery.RequirementsResult:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/grocery-requirements/preview",
            TypeAdapter(grocery.RequirementsResult),
            body=data.model_dump(mode="json"),
        )

    def grocery_shortages(
        self, household_id: uuid.UUID, data: grocery.RequirementsInput
    ) -> grocery.ShortageResult:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/grocery-requirements/shortage-preview",
            TypeAdapter(grocery.ShortageResult),
            body=data.model_dump(mode="json"),
        )

    def generate_grocery_list(
        self, household_id: uuid.UUID, list_id: uuid.UUID, data: grocery.GenerationInput
    ) -> grocery.GenerationResult:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/grocery-lists/{list_id}/generations",
            TypeAdapter(grocery.GenerationResult),
            body=data.model_dump(mode="json"),
        )

    def purchase_grocery_item(
        self,
        household_id: uuid.UUID,
        list_id: uuid.UUID,
        item_id: uuid.UUID,
        data: grocery.PurchaseInput,
    ) -> grocery.PurchaseResult:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/grocery-lists/{list_id}/items/{item_id}/purchase",
            TypeAdapter(grocery.PurchaseResult),
            body=data.model_dump(mode="json"),
        )

    def stored_food(self, food_id: uuid.UUID) -> StoredFood:
        return self._request("GET", f"/v1/foods/{food_id}", TypeAdapter(StoredFood))

    def pantry_locations(self, household_id: uuid.UUID) -> list[LocationRecord]:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/pantry/locations",
            TypeAdapter(list[LocationRecord]),
        )

    def create_pantry_location(
        self, household_id: uuid.UUID, data: LocationInput
    ) -> LocationRecord:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/pantry/locations",
            TypeAdapter(LocationRecord),
            body=data.model_dump(mode="json"),
            expected_status=201,
        )

    def delete_pantry_location(self, household_id: uuid.UUID, location_id: uuid.UUID) -> None:
        self._request(
            "DELETE",
            f"/v1/households/{household_id}/pantry/locations/{location_id}",
            TypeAdapter(type(None)),
            expected_status=204,
        )

    def pantry_items(self, household_id: uuid.UUID) -> list[PantryLot]:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/pantry/items",
            TypeAdapter(list[PantryLot]),
            retry_safe=False,
        )

    def pantry_overview(self, household_id: uuid.UUID) -> PantryOverview:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/pantry/summary",
            TypeAdapter(PantryOverview),
            retry_safe=False,
        )

    def pantry_expiring(self, household_id: uuid.UUID) -> list[PantryLot]:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/pantry/expiring",
            TypeAdapter(list[PantryLot]),
            retry_safe=False,
        )

    def pantry_expired(self, household_id: uuid.UUID) -> list[PantryLot]:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/pantry/expired",
            TypeAdapter(list[PantryLot]),
            retry_safe=False,
        )

    def pantry_low_stock(self, household_id: uuid.UUID) -> list[StockRule]:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/pantry/low-stock",
            TypeAdapter(list[StockRule]),
            retry_safe=False,
        )

    def pantry_stock_rules(self, household_id: uuid.UUID) -> list[StockRule]:
        return self._request(
            "GET", f"/v1/households/{household_id}/pantry/stock-rules", TypeAdapter(list[StockRule])
        )

    def save_pantry_stock_rule(
        self, household_id: uuid.UUID, food_id: uuid.UUID, data: StockRuleInput
    ) -> StockRule:
        return self._request(
            "PUT",
            f"/v1/households/{household_id}/pantry/stock-rules/{food_id}",
            TypeAdapter(StockRule),
            body=data.model_dump(mode="json"),
        )

    def create_pantry_item(self, household_id: uuid.UUID, data: PantryItemInput) -> PantryLot:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/pantry/items",
            TypeAdapter(PantryLot),
            body=data.model_dump(mode="json"),
            expected_status=201,
        )

    def adjust_pantry_item(
        self, household_id: uuid.UUID, item_id: uuid.UUID, data: AdjustmentInput
    ) -> PantryLot:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/pantry/items/{item_id}/adjust",
            TypeAdapter(PantryLot),
            body=data.model_dump(mode="json"),
        )

    def discard_pantry_item(
        self, household_id: uuid.UUID, item_id: uuid.UUID, data: AdjustmentInput
    ) -> PantryLot:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/pantry/items/{item_id}/discard",
            TypeAdapter(PantryLot),
            body=data.model_dump(mode="json"),
        )

    def consume_pantry(self, household_id: uuid.UUID, data: ConsumeInput) -> list[PantryLot]:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/pantry/consume",
            TypeAdapter(list[PantryLot]),
            body=data.model_dump(mode="json"),
        )

    def transfer_pantry(self, household_id: uuid.UUID, data: TransferInput) -> PantryLot:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/pantry/transfer",
            TypeAdapter(PantryLot),
            body=data.model_dump(mode="json"),
        )

    def get_recipe(self, household_id: uuid.UUID, recipe_id: uuid.UUID) -> RecipeRecord:
        return self._request(
            "GET", f"/v1/households/{household_id}/recipes/{recipe_id}", TypeAdapter(RecipeRecord)
        )

    def create_recipe(
        self, household_id: uuid.UUID, recipe: RecipeInput, idempotency_key: str
    ) -> RecipeRecord:
        return self._request(
            "POST",
            f"/v1/households/{household_id}/recipes",
            TypeAdapter(RecipeRecord),
            body=recipe.model_dump(mode="json"),
            expected_status=201,
            headers={"Idempotency-Key": idempotency_key},
        )

    def update_recipe(
        self,
        household_id: uuid.UUID,
        recipe_id: uuid.UUID,
        recipe: RecipeInput,
        expected_version: int,
    ) -> RecipeRecord:
        return self._request(
            "PUT",
            f"/v1/households/{household_id}/recipes/{recipe_id}",
            TypeAdapter(RecipeRecord),
            body={**recipe.model_dump(mode="json"), "expected_version": expected_version},
        )

    def delete_recipe(
        self, household_id: uuid.UUID, recipe_id: uuid.UUID, expected_version: int
    ) -> None:
        self._request(
            "DELETE",
            f"/v1/households/{household_id}/recipes/{recipe_id}?expected_version={expected_version}",
            TypeAdapter(type(None)),
            expected_status=204,
        )

    def recipe_nutrition(self, household_id: uuid.UUID, recipe_id: uuid.UUID) -> RecipeNutrition:
        return self._request(
            "GET",
            f"/v1/households/{household_id}/recipes/{recipe_id}/nutrition",
            TypeAdapter(RecipeNutrition),
        )


def create_api_client() -> APIClient:
    try:
        settings = UISettings()
    except ValidationError:
        raise APIProtocolError(
            "invalid_api_url", "Check the APP_API_BASE_URL configuration."
        ) from None
    return APIClient(settings.api_base_url)
