import uuid

from fastapi import Depends, FastAPI, Request, Response, status
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
from nourish_nest.nutrition import UnsupportedProfileError, calculate_nutrition_plan
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


@app.exception_handler(SQLAlchemyError)
async def database_failure(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    body = ErrorBody(
        code="internal_error", message="Internal server error", request_id=request.state.request_id
    )
    return JSONResponse(status_code=500, content=body.model_dump())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/v1/nutrition/calculate", response_model=NutritionPlan)
def nutrition_calculate(profile: NutritionProfile) -> NutritionPlan:
    return calculate_nutrition_plan(profile)


@app.post("/v1/households", response_model=HouseholdResponse, status_code=status.HTTP_201_CREATED)
def create_household(data: HouseholdCreate, db: Session = DB_DEPENDENCY) -> HouseholdResponse:
    return HouseholdService(db).create_household(data)


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
