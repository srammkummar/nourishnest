import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nourish_nest import __version__
from nourish_nest.database import get_db
from nourish_nest.domain import ErrorBody, NutritionPlan, NutritionProfile
from nourish_nest.food_schemas import (
    FoodCreate,
    FoodResponse,
    FoodSearchResponse,
    FoodUpdate,
    RecipeCreate,
    RecipeNutritionResponse,
    RecipeResponse,
    RecipeUpdate,
)
from nourish_nest.food_services import UnsupportedConversionError
from nourish_nest.grocery_generation_schemas import (
    GroceryGenerationRequest,
    GroceryGenerationResponse,
)
from nourish_nest.grocery_generation_services import (
    GroceryGenerationError,
    GroceryGenerationService,
)
from nourish_nest.grocery_purchase_schemas import (
    GroceryPurchaseRequest,
    GroceryPurchaseResponse,
)
from nourish_nest.grocery_purchase_services import (
    GroceryPurchaseError,
    GroceryPurchaseService,
)
from nourish_nest.grocery_requirement_schemas import (
    GroceryRequirementsRequest,
    GroceryRequirementsResponse,
)
from nourish_nest.grocery_requirement_services import GroceryRequirementsService
from nourish_nest.grocery_schemas import (
    GroceryItemCreate,
    GroceryItemResponse,
    GroceryItemUpdate,
    GroceryListCreate,
    GroceryListResponse,
    GroceryListUpdate,
)
from nourish_nest.grocery_services import GroceryService, StaleGroceryVersionError
from nourish_nest.grocery_shortage_schemas import GroceryShortageResponse
from nourish_nest.grocery_shortage_services import GroceryShortageService
from nourish_nest.nutrition import UnsupportedProfileError, calculate_nutrition_plan
from nourish_nest.pantry_schemas import (
    PantryAdjustment,
    PantryConsumeRequest,
    PantryDiscard,
    PantryItemCreate,
    PantryItemResponse,
    PantryItemUpdate,
    PantryLocationCreate,
    PantryLocationResponse,
    PantryStockRuleFields,
    PantryStockRuleResponse,
    PantrySummaryResponse,
    PantryTransferRequest,
)
from nourish_nest.pantry_services import (
    PantryError,
    PantryService,
)
from nourish_nest.providers import (
    ExternalFoodNotFoundError,
    FoodDataProvider,
    InvalidProviderResponseError,
    ProviderFood,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderSearchResponse,
    ProviderUnavailableError,
    get_food_data_provider,
)
from nourish_nest.schemas import (
    HouseholdCreate,
    HouseholdResponse,
    MemberCreate,
    MemberResponse,
    MemberUpdate,
)
from nourish_nest.services import (
    ConflictError,
    FoodService,
    ForbiddenError,
    HouseholdService,
    NotFoundError,
    RecipeService,
)

app = FastAPI(title="NourishNest API", version=__version__)
DB_DEPENDENCY = Depends(get_db)
PROVIDER_DEPENDENCY = Depends(get_food_data_provider)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(UnsupportedProfileError)
async def unsupported_profile(request: Request, exc: UnsupportedProfileError) -> JSONResponse:
    body = ErrorBody(
        code="unsupported_profile",
        message=str(exc),
        request_id=request.state.request_id,
    )
    return JSONResponse(status_code=422, content=body.model_dump())


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    first_error = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first_error.get("loc", []))
    detail = first_error.get("msg", "Request validation failed")
    body = ErrorBody(
        code="invalid_request",
        message=f"{location}: {detail}" if location else detail,
        request_id=request.state.request_id,
    )
    return JSONResponse(status_code=422, content=body.model_dump())


@app.exception_handler(NotFoundError)
async def missing_record(request: Request, exc: NotFoundError) -> JSONResponse:
    body = ErrorBody(code="not_found", message=str(exc), request_id=request.state.request_id)
    return JSONResponse(status_code=404, content=body.model_dump())


@app.exception_handler(LookupError)
async def missing_pantry_record(request: Request, exc: LookupError) -> JSONResponse:
    body = ErrorBody(code="not_found", message=str(exc), request_id=request.state.request_id)
    return JSONResponse(status_code=404, content=body.model_dump())


@app.exception_handler(ForbiddenError)
async def forbidden(request: Request, exc: ForbiddenError) -> JSONResponse:
    body = ErrorBody(code="forbidden", message=str(exc), request_id=request.state.request_id)
    return JSONResponse(status_code=403, content=body.model_dump())


@app.exception_handler(ConflictError)
async def conflict(request: Request, exc: ConflictError) -> JSONResponse:
    body = ErrorBody(code="conflict", message=str(exc), request_id=request.state.request_id)
    return JSONResponse(status_code=409, content=body.model_dump())


@app.exception_handler(UnsupportedConversionError)
async def unsupported_conversion(request: Request, exc: UnsupportedConversionError) -> JSONResponse:
    body = ErrorBody(code="unsupported_conversion", message=str(exc), request_id=request.state.request_id)
    return JSONResponse(status_code=422, content=body.model_dump())


def _provider_error(code: str, message: str, request: Request, status_code: int) -> JSONResponse:
    body = ErrorBody(code=code, message=message, request_id=request.state.request_id)
    return JSONResponse(status_code=status_code, content=body.model_dump())


@app.exception_handler(ProviderNotConfiguredError)
async def provider_not_configured(request: Request, exc: ProviderNotConfiguredError) -> JSONResponse:
    return _provider_error(exc.code, str(exc), request, 503)


@app.exception_handler(ProviderUnavailableError)
async def provider_unavailable(request: Request, exc: ProviderUnavailableError) -> JSONResponse:
    return _provider_error(exc.code, str(exc), request, 503)


@app.exception_handler(ProviderRateLimitedError)
async def provider_rate_limited(request: Request, exc: ProviderRateLimitedError) -> JSONResponse:
    return _provider_error(exc.code, str(exc), request, 429)


@app.exception_handler(ExternalFoodNotFoundError)
async def external_food_not_found(request: Request, exc: ExternalFoodNotFoundError) -> JSONResponse:
    return _provider_error(exc.code, str(exc), request, 404)


@app.exception_handler(InvalidProviderResponseError)
async def invalid_provider_response(request: Request, exc: InvalidProviderResponseError) -> JSONResponse:
    return _provider_error(exc.code, str(exc), request, 502)


@app.exception_handler(PantryError)
async def pantry_error(request: Request, exc: PantryError) -> JSONResponse:
    status_codes = {
        "insufficient_inventory": 409,
        "incompatible_units": 422,
        "unsupported_conversion": 422,
        "stale_inventory_version": 409,
        "duplicate_idempotency_key": 409,
        "pantry_location_not_empty": 409,
        "expired_inventory": 409,
        "invalid_transfer": 422,
    }
    return _provider_error(exc.code, str(exc), request, status_codes.get(exc.code, 409))


@app.exception_handler(StaleGroceryVersionError)
async def stale_grocery(request: Request, exc: StaleGroceryVersionError) -> JSONResponse:
    return _provider_error(exc.code, str(exc), request, 409)


@app.exception_handler(GroceryGenerationError)
async def grocery_generation_error(request: Request, exc: GroceryGenerationError) -> JSONResponse:
    return _provider_error(exc.code, str(exc), request, 422 if exc.code == "invalid_request" else 409)


@app.exception_handler(GroceryPurchaseError)
async def grocery_purchase_error(request: Request, exc: GroceryPurchaseError) -> JSONResponse:
    status_code = 409 if exc.code in {"idempotency_conflict", "overpurchase_not_allowed"} else 422
    return _provider_error(exc.code, str(exc), request, status_code)


@app.exception_handler(SQLAlchemyError)
async def database_failure(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    body = ErrorBody(
        code="internal_error", message="Internal server error", request_id=request.state.request_id
    )
    return JSONResponse(status_code=500, content=body.model_dump())


@app.post(
    "/v1/households/{household_id}/grocery-requirements/preview",
    response_model=GroceryRequirementsResponse,
)
def preview_grocery_requirements(
    household_id: uuid.UUID, data: GroceryRequirementsRequest, db: Session = DB_DEPENDENCY
):
    return GroceryRequirementsService(db).preview(household_id, data)


@app.post(
    "/v1/households/{household_id}/grocery-requirements/shortage-preview",
    response_model=GroceryShortageResponse,
)
def preview_grocery_shortage(
    household_id: uuid.UUID, data: GroceryRequirementsRequest, db: Session = DB_DEPENDENCY
):
    return GroceryShortageService(db).preview(household_id, data)


@app.post(
    "/v1/households/{household_id}/grocery-lists/{list_id}/generations",
    response_model=GroceryGenerationResponse,
)
def generate_grocery_list(
    household_id: uuid.UUID, list_id: uuid.UUID, data: GroceryGenerationRequest,
    db: Session = DB_DEPENDENCY,
):
    return GroceryGenerationService(db).generate(household_id, list_id, data)


@app.post(
    "/v1/households/{household_id}/grocery-lists/{list_id}/items/{item_id}/purchase",
    response_model=GroceryPurchaseResponse,
)
def purchase_grocery_item(
    household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID,
    data: GroceryPurchaseRequest, db: Session = DB_DEPENDENCY,
):
    return GroceryPurchaseService(db).purchase(household_id, list_id, item_id, data)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/v1/nutrition/calculate", response_model=NutritionPlan)
def nutrition_calculate(profile: NutritionProfile) -> NutritionPlan:
    return calculate_nutrition_plan(profile)


@app.post("/v1/households", response_model=HouseholdResponse, status_code=status.HTTP_201_CREATED)
def create_household(data: HouseholdCreate, db: Session = DB_DEPENDENCY) -> HouseholdResponse:
    return HouseholdService(db).create_household(data)


@app.get("/v1/households", response_model=list[HouseholdResponse])
def list_households(db: Session = DB_DEPENDENCY) -> list[HouseholdResponse]:
    return HouseholdService(db).list_households()


@app.get("/v1/households/{household_id}", response_model=HouseholdResponse)
def get_household(household_id: uuid.UUID, db: Session = DB_DEPENDENCY) -> HouseholdResponse:
    return HouseholdService(db).get_household(household_id)


@app.delete("/v1/households/{household_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_household(household_id: uuid.UUID, db: Session = DB_DEPENDENCY) -> Response:
    HouseholdService(db).delete_household(household_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/v1/households/{household_id}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_member(
    household_id: uuid.UUID, data: MemberCreate, db: Session = DB_DEPENDENCY
) -> MemberResponse:
    return HouseholdService(db).create_member(household_id, data)


@app.get("/v1/households/{household_id}/members", response_model=list[MemberResponse])
def list_members(household_id: uuid.UUID, db: Session = DB_DEPENDENCY) -> list[MemberResponse]:
    return HouseholdService(db).list_members(household_id)


@app.get("/v1/members/{member_id}", response_model=MemberResponse)
def get_member(member_id: uuid.UUID, db: Session = DB_DEPENDENCY) -> MemberResponse:
    return HouseholdService(db).get_member(member_id)


@app.put("/v1/members/{member_id}", response_model=MemberResponse)
def update_member(
    member_id: uuid.UUID, data: MemberUpdate, db: Session = DB_DEPENDENCY
) -> MemberResponse:
    return HouseholdService(db).update_member(member_id, data)


@app.delete("/v1/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_member(member_id: uuid.UUID, db: Session = DB_DEPENDENCY) -> Response:
    HouseholdService(db).delete_member(member_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/v1/members/{member_id}/nutrition/calculate", response_model=NutritionPlan)
def calculate_saved_member(member_id: uuid.UUID, db: Session = DB_DEPENDENCY) -> NutritionPlan:
    return HouseholdService(db).calculate_member_nutrition(member_id)


@app.post("/v1/foods", response_model=FoodResponse, status_code=status.HTTP_201_CREATED)
def create_food(data: FoodCreate, db: Session = DB_DEPENDENCY) -> FoodResponse:
    return FoodService(db).create(data)


@app.get("/v1/foods", response_model=FoodSearchResponse)
def list_foods(q: str | None = None, db: Session = DB_DEPENDENCY) -> FoodSearchResponse:
    return FoodSearchResponse(foods=FoodService(db).list(q))


@app.get("/v1/foods/search", response_model=FoodSearchResponse)
def search_foods(q: str, db: Session = DB_DEPENDENCY) -> FoodSearchResponse:
    return FoodSearchResponse(foods=FoodService(db).list(q))


@app.get("/v1/foods/{food_id}", response_model=FoodResponse)
def get_food(food_id: uuid.UUID, db: Session = DB_DEPENDENCY) -> FoodResponse:
    return FoodService(db).get(food_id)


@app.put("/v1/foods/{food_id}", response_model=FoodResponse)
def update_food(
    food_id: uuid.UUID, data: FoodUpdate, db: Session = DB_DEPENDENCY
) -> FoodResponse:
    return FoodService(db).update(food_id, data)


@app.delete("/v1/foods/{food_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_food(food_id: uuid.UUID, db: Session = DB_DEPENDENCY) -> Response:
    FoodService(db).delete(food_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/v1/households/{household_id}/recipes",
    response_model=RecipeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_recipe(
    household_id: uuid.UUID, data: RecipeCreate, db: Session = DB_DEPENDENCY
) -> RecipeResponse:
    return RecipeService(db).create(household_id, data)


@app.get("/v1/households/{household_id}/recipes", response_model=list[RecipeResponse])
def list_recipes(household_id: uuid.UUID, db: Session = DB_DEPENDENCY) -> list[RecipeResponse]:
    return RecipeService(db).list(household_id)


@app.get("/v1/households/{household_id}/recipes/{recipe_id}", response_model=RecipeResponse)
def get_recipe(
    household_id: uuid.UUID, recipe_id: uuid.UUID, db: Session = DB_DEPENDENCY
) -> RecipeResponse:
    return RecipeService(db).get(household_id, recipe_id)


@app.put("/v1/households/{household_id}/recipes/{recipe_id}", response_model=RecipeResponse)
def update_recipe(
    household_id: uuid.UUID,
    recipe_id: uuid.UUID,
    data: RecipeUpdate,
    db: Session = DB_DEPENDENCY,
) -> RecipeResponse:
    return RecipeService(db).update(household_id, recipe_id, data)


@app.delete(
    "/v1/households/{household_id}/recipes/{recipe_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_recipe(
    household_id: uuid.UUID, recipe_id: uuid.UUID, db: Session = DB_DEPENDENCY
) -> Response:
    RecipeService(db).delete(household_id, recipe_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get(
    "/v1/households/{household_id}/recipes/{recipe_id}/nutrition",
    response_model=RecipeNutritionResponse,
)
def recipe_nutrition(
    household_id: uuid.UUID, recipe_id: uuid.UUID, db: Session = DB_DEPENDENCY
) -> RecipeNutritionResponse:
    return RecipeService(db).nutrition(household_id, recipe_id)


@app.get("/v1/providers/usda/search", response_model=ProviderSearchResponse)
def usda_search(
    q: str, page_size: int = 25, provider: FoodDataProvider = PROVIDER_DEPENDENCY
):
    return provider.search(q, page_size)


@app.get("/v1/providers/usda/foods/{fdc_id}", response_model=ProviderFood)
def usda_food(fdc_id: int, provider: FoodDataProvider = PROVIDER_DEPENDENCY):
    return provider.get_food(fdc_id)


@app.post("/v1/foods/import/usda/{fdc_id}", response_model=FoodResponse, status_code=status.HTTP_201_CREATED)
def import_usda_food(
    fdc_id: int, db: Session = DB_DEPENDENCY, provider: FoodDataProvider = PROVIDER_DEPENDENCY
) -> FoodResponse:
    return FoodService(db).import_usda(provider, fdc_id)


@app.post("/v1/foods/{food_id}/refresh/usda", response_model=FoodResponse)
def refresh_usda_food(
    food_id: uuid.UUID,
    db: Session = DB_DEPENDENCY,
    provider: FoodDataProvider = PROVIDER_DEPENDENCY,
) -> FoodResponse:
    return FoodService(db).refresh_usda(provider, food_id)


def pantry_service(db: Session) -> PantryService:
    from nourish_nest.config import get_settings

    return PantryService(db, get_settings().pantry_expiring_soon_days)


@app.post("/v1/households/{household_id}/pantry/locations", response_model=PantryLocationResponse, status_code=201)
def create_pantry_location(household_id: uuid.UUID, data: PantryLocationCreate, db: Session = DB_DEPENDENCY):
    return pantry_service(db).create_location(household_id, data)


@app.get("/v1/households/{household_id}/pantry/locations", response_model=list[PantryLocationResponse])
def list_pantry_locations(household_id: uuid.UUID, db: Session = DB_DEPENDENCY):
    return pantry_service(db).list_locations(household_id)


@app.put("/v1/households/{household_id}/pantry/locations/{location_id}", response_model=PantryLocationResponse)
def update_pantry_location(household_id: uuid.UUID, location_id: uuid.UUID, data: PantryLocationCreate, db: Session = DB_DEPENDENCY):
    return pantry_service(db).update_location(household_id, location_id, data)


@app.delete("/v1/households/{household_id}/pantry/locations/{location_id}", status_code=204)
def delete_pantry_location(household_id: uuid.UUID, location_id: uuid.UUID, db: Session = DB_DEPENDENCY):
    pantry_service(db).delete_location(household_id, location_id)
    return Response(status_code=204)


@app.post("/v1/households/{household_id}/pantry/items", response_model=PantryItemResponse, status_code=201)
def create_pantry_item(household_id: uuid.UUID, data: PantryItemCreate, db: Session = DB_DEPENDENCY):
    return pantry_service(db).create_item(household_id, data)


@app.get("/v1/households/{household_id}/pantry/items", response_model=list[PantryItemResponse])
def list_pantry_items(household_id: uuid.UUID, db: Session = DB_DEPENDENCY):
    return pantry_service(db).list_items(household_id)


@app.get("/v1/households/{household_id}/pantry/items/{item_id}", response_model=PantryItemResponse)
def get_pantry_item(household_id: uuid.UUID, item_id: uuid.UUID, db: Session = DB_DEPENDENCY):
    return pantry_service(db).get_item(household_id, item_id)


@app.put("/v1/households/{household_id}/pantry/items/{item_id}", response_model=PantryItemResponse)
def update_pantry_item(household_id: uuid.UUID, item_id: uuid.UUID, data: PantryItemUpdate, db: Session = DB_DEPENDENCY):
    return pantry_service(db).update_item(household_id, item_id, data)


@app.post("/v1/households/{household_id}/pantry/items/{item_id}/adjust", response_model=PantryItemResponse)
def adjust_pantry_item(household_id: uuid.UUID, item_id: uuid.UUID, data: PantryAdjustment, db: Session = DB_DEPENDENCY):
    return pantry_service(db).adjust(household_id, item_id, data)


@app.post("/v1/households/{household_id}/pantry/items/{item_id}/discard", response_model=PantryItemResponse)
def discard_pantry_item(household_id: uuid.UUID, item_id: uuid.UUID, data: PantryDiscard, db: Session = DB_DEPENDENCY):
    return pantry_service(db).discard(household_id, item_id, data)


@app.post("/v1/households/{household_id}/pantry/consume", response_model=list[PantryItemResponse])
def consume_pantry(household_id: uuid.UUID, data: PantryConsumeRequest, db: Session = DB_DEPENDENCY):
    return pantry_service(db).consume(household_id, data)


@app.post("/v1/households/{household_id}/pantry/transfer", response_model=PantryItemResponse)
def transfer_pantry(household_id: uuid.UUID, data: PantryTransferRequest, db: Session = DB_DEPENDENCY):
    return pantry_service(db).transfer(household_id, data)


@app.get("/v1/households/{household_id}/pantry/expiring", response_model=list[PantryItemResponse])
def expiring_pantry(household_id: uuid.UUID, db: Session = DB_DEPENDENCY):
    return pantry_service(db).expiring(household_id)


@app.get("/v1/households/{household_id}/pantry/expired", response_model=list[PantryItemResponse])
def expired_pantry(household_id: uuid.UUID, db: Session = DB_DEPENDENCY):
    return pantry_service(db).expired(household_id)


@app.get("/v1/households/{household_id}/pantry/low-stock", response_model=list[PantryStockRuleResponse])
def low_stock_pantry(household_id: uuid.UUID, db: Session = DB_DEPENDENCY):
    return pantry_service(db).low_stock(household_id)


@app.get("/v1/households/{household_id}/pantry/summary", response_model=PantrySummaryResponse)
def pantry_summary(household_id: uuid.UUID, db: Session = DB_DEPENDENCY):
    counts, low_stock = pantry_service(db).summary(household_id)
    from nourish_nest.models import PantryItemStatus

    return PantrySummaryResponse(
        active_items=counts[PantryItemStatus.ACTIVE],
        depleted_items=counts[PantryItemStatus.DEPLETED],
        expired_items=counts[PantryItemStatus.EXPIRED],
        discarded_items=counts[PantryItemStatus.DISCARDED],
        low_stock_food_ids=low_stock,
    )


@app.put("/v1/households/{household_id}/pantry/stock-rules/{food_id}", response_model=PantryStockRuleResponse)
def upsert_stock_rule(household_id: uuid.UUID, food_id: uuid.UUID, data: PantryStockRuleFields, db: Session = DB_DEPENDENCY):
    return pantry_service(db).upsert_stock_rule(household_id, food_id, data)


@app.get("/v1/households/{household_id}/pantry/stock-rules", response_model=list[PantryStockRuleResponse])
def list_stock_rules(household_id: uuid.UUID, db: Session = DB_DEPENDENCY):
    return pantry_service(db).list_stock_rules(household_id)


@app.delete("/v1/households/{household_id}/pantry/stock-rules/{food_id}", status_code=204)
def delete_stock_rule(household_id: uuid.UUID, food_id: uuid.UUID, db: Session = DB_DEPENDENCY):
    pantry_service(db).delete_stock_rule(household_id, food_id)
    return Response(status_code=204)


@app.post("/v1/households/{household_id}/grocery-lists", response_model=GroceryListResponse, status_code=201)
def create_grocery_list(
    household_id: uuid.UUID, data: GroceryListCreate, db: Session = DB_DEPENDENCY
):
    return GroceryService(db).create_list(household_id, data)


@app.get("/v1/households/{household_id}/grocery-lists", response_model=list[GroceryListResponse])
def list_grocery_lists(
    household_id: uuid.UUID, db: Session = DB_DEPENDENCY
):
    return GroceryService(db).list_lists(household_id)


@app.get("/v1/households/{household_id}/grocery-lists/{list_id}", response_model=GroceryListResponse)
def get_grocery_list(
    household_id: uuid.UUID, list_id: uuid.UUID, db: Session = DB_DEPENDENCY
):
    return GroceryService(db).get_list(household_id, list_id)


@app.put("/v1/households/{household_id}/grocery-lists/{list_id}", response_model=GroceryListResponse)
def update_grocery_list(
    household_id: uuid.UUID, list_id: uuid.UUID, data: GroceryListUpdate, db: Session = DB_DEPENDENCY
):
    return GroceryService(db).update_list(household_id, list_id, data)


@app.delete("/v1/households/{household_id}/grocery-lists/{list_id}", status_code=204)
def delete_grocery_list(
    household_id: uuid.UUID, list_id: uuid.UUID, expected_version: Annotated[int, Query(ge=1)], db: Session = DB_DEPENDENCY
):
    GroceryService(db).delete_list(household_id, list_id, expected_version)
    return Response(status_code=204)


@app.post("/v1/households/{household_id}/grocery-lists/{list_id}/items", response_model=GroceryItemResponse, status_code=201)
def create_grocery_item(
    household_id: uuid.UUID, list_id: uuid.UUID, data: GroceryItemCreate, db: Session = DB_DEPENDENCY
):
    return GroceryService(db).create_item(household_id, list_id, data)


@app.get("/v1/households/{household_id}/grocery-lists/{list_id}/items", response_model=list[GroceryItemResponse])
def list_grocery_items(
    household_id: uuid.UUID, list_id: uuid.UUID, db: Session = DB_DEPENDENCY
):
    return GroceryService(db).list_items(household_id, list_id)


@app.get("/v1/households/{household_id}/grocery-lists/{list_id}/items/{item_id}", response_model=GroceryItemResponse)
def get_grocery_item(
    household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID, db: Session = DB_DEPENDENCY
):
    return GroceryService(db).get_item(household_id, list_id, item_id)


@app.put("/v1/households/{household_id}/grocery-lists/{list_id}/items/{item_id}", response_model=GroceryItemResponse)
def update_grocery_item(
    household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID, data: GroceryItemUpdate, db: Session = DB_DEPENDENCY
):
    return GroceryService(db).update_item(household_id, list_id, item_id, data)


@app.delete("/v1/households/{household_id}/grocery-lists/{list_id}/items/{item_id}", status_code=204)
def delete_grocery_item(
    household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID, expected_version: Annotated[int, Query(ge=1)], db: Session = DB_DEPENDENCY
):
    GroceryService(db).delete_item(household_id, list_id, item_id, expected_version)
    return Response(status_code=204)
