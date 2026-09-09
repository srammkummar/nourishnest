import hashlib
import json
import uuid
from datetime import UTC
from decimal import Decimal, localcontext

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from nourish_nest.grocery_generation_repositories import GroceryGenerationRepository
from nourish_nest.grocery_generation_schemas import (
    GeneratedGroceryItemResponse,
    GroceryGenerationRequest,
    GroceryGenerationResponse,
)
from nourish_nest.grocery_requirement_schemas import GroceryRequirementsRequest
from nourish_nest.grocery_services import StaleGroceryVersionError
from nourish_nest.grocery_shortage_services import GroceryShortageService
from nourish_nest.models import (
    GroceryGenerationRun,
    GroceryItemRecipeSource,
    GroceryItemSourceType,
    GroceryListItem,
    GroceryListStatus,
    utc_now,
)
from nourish_nest.repositories import HouseholdRepository
from nourish_nest.services import NotFoundError


class GroceryGenerationError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def canonical_request_hash(data: GroceryGenerationRequest) -> str:
    canonical = {
        "expected_list_version": data.expected_list_version,
        "recipes": [{"recipe_id": str(row.recipe_id),
                     "desired_servings": format(row.desired_servings.normalize(), "f")}
                    for row in sorted(data.recipes, key=lambda row: row.recipe_id)],
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _persistable(quantity: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        if (not quantity.is_finite() or quantity < 0 or quantity >= Decimal(10**12)
                or quantity != quantity.quantize(Decimal("0.000001"))):
            raise GroceryGenerationError(
                "invalid_request", "Calculated quantities must fit NUMERIC(18,6) exactly; no rounding was applied."
            )
    return quantity


class GroceryGenerationService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = GroceryGenerationRepository(session)
        self.households = HouseholdRepository(session)

    def _existing(self, household_id, list_id, key, request_hash):
        run = self.repo.run(household_id, list_id, key)
        if run is not None:
            if run.request_hash != request_hash:
                raise GroceryGenerationError("idempotency_conflict", "Idempotency key was used for a different request")
            return run
        if self.repo.run(household_id, list_id) is not None:
            raise GroceryGenerationError("grocery_generation_exists", "This list already has a recipe generation")
        return None

    def _response(self, listing, run, replayed, warnings=()):
        items = []
        for item in sorted(self.repo.items(run), key=lambda row: (
            row.display_name.casefold(), str(row.food_id), row.required_unit, row.id
        )):
            result = GeneratedGroceryItemResponse.model_validate(item)
            result.recipe_sources.sort(key=lambda row: (row.recipe_id, str(row.recipe_ingredient_id), row.id))
            items.append(result)
        return GroceryGenerationResponse(
            generation_run_id=run.id, grocery_list_id=listing.id, grocery_list_version=listing.version,
            replayed=replayed, created_items=items, warnings=list(warnings), warnings_available=not replayed,
            calculation_as_of=run.created_at.replace(tzinfo=UTC) if run.created_at.tzinfo is None else run.created_at,
        )

    def generate(self, household_id: uuid.UUID, list_id: uuid.UUID, data: GroceryGenerationRequest):
        request_hash = canonical_request_hash(data)
        try:
            with self.session.no_autoflush:
                if self.households.get(household_id) is None:
                    raise NotFoundError("Household not found")
                listing = self.repo.get_list(household_id, list_id, lock=True)
                if listing is None:
                    raise NotFoundError("Grocery list not found")
                existing = self._existing(household_id, list_id, data.idempotency_key, request_hash)
                if existing is not None:
                    response = self._response(listing, existing, True)
                    self.session.rollback()  # End the read/lock transaction without any writes.
                    return response
                if listing.status not in {GroceryListStatus.DRAFT, GroceryListStatus.ACTIVE}:
                    raise GroceryGenerationError("invalid_grocery_list_status", "Only draft or active lists can be generated")
                if listing.version != data.expected_list_version:
                    raise StaleGroceryVersionError("Grocery record version is stale")
                # ORM version-qualified UPDATE serializes generation attempts before persisting children.
                listing.updated_at = utc_now()
                listing.version += 1
                self.session.flush()
                preview = GroceryShortageService(self.session).preview(
                    household_id, GroceryRequirementsRequest(recipes=data.recipes)
                )
                run = GroceryGenerationRun(
                    household_id=household_id, grocery_list_id=list_id, idempotency_key=data.idempotency_key,
                    request_hash=request_hash, calculation_version="grocery-generation-v1",
                    created_at=preview.calculation_as_of,
                )
                self.session.add(run)
                for requirement in preview.requirements:
                    if not requirement.purchase_required or requirement.shortage_quantity <= 0:
                        continue
                    item = GroceryListItem(
                        grocery_list_id=list_id, generation_run=run, food_id=requirement.food_id,
                        display_name=requirement.food_name, required_quantity=_persistable(requirement.shortage_quantity),
                        required_unit=requirement.canonical_unit, purchased_quantity=Decimal(0), checked=False,
                        source_type=GroceryItemSourceType.RECIPE,
                    )
                    item.recipe_sources = [GroceryItemRecipeSource(
                        recipe_id=source.recipe_id, recipe_ingredient_id=source.ingredient_id,
                        required_quantity=_persistable(source.required_quantity), canonical_unit=requirement.canonical_unit,
                    ) for source in requirement.sources]
                    self.session.add(item)
                self.session.flush()
                response = self._response(listing, run, False, preview.warnings)
                self.session.commit()
                return response
        except (IntegrityError, StaleDataError) as exc:
            self.session.rollback()
            try:
                # Resolve the committed winner using the database after a failed flush/commit.
                existing = self._existing(household_id, list_id, data.idempotency_key, request_hash)
                listing = self.repo.get_list(household_id, list_id)
                if existing is not None and listing is not None:
                    return self._response(listing, existing, True)
                if isinstance(exc, StaleDataError):
                    raise StaleGroceryVersionError("Grocery record version is stale") from exc
                raise
            finally:
                self.session.rollback()
        except Exception:
            self.session.rollback()
            raise
