import uuid

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nourish_nest import __version__
from nourish_nest.database import get_db
from nourish_nest.domain import ErrorBody, NutritionPlan, NutritionProfile
from nourish_nest.nutrition import UnsupportedProfileError, calculate_nutrition_plan
from nourish_nest.schemas import (
    HouseholdCreate,
    HouseholdResponse,
    MemberCreate,
    MemberResponse,
    MemberUpdate,
)
from nourish_nest.services import HouseholdService, NotFoundError

app = FastAPI(title="NourishNest API", version=__version__)
DB_DEPENDENCY = Depends(get_db)


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
