"""HTTP-only UI boundary. Keep these wire models independent of server ORM schemas."""

import time
import uuid
from collections.abc import Callable
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

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
                    headers={"x-request-id": request_id},
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
                return adapter.validate_python(response.json())
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


def create_api_client() -> APIClient:
    try:
        settings = UISettings()
    except ValidationError:
        raise APIProtocolError(
            "invalid_api_url", "Check the APP_API_BASE_URL configuration."
        ) from None
    return APIClient(settings.api_base_url)
