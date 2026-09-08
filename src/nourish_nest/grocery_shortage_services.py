import uuid
from decimal import Decimal, localcontext

from sqlalchemy.orm import Session

from nourish_nest.food_services import UnsupportedConversionError, convert_quantity
from nourish_nest.grocery_requirement_schemas import GroceryRequirementsRequest, RequirementWarning
from nourish_nest.grocery_requirement_services import GroceryRequirementsService
from nourish_nest.grocery_shortage_schemas import (
    GroceryShortage,
    GroceryShortageResponse,
    PantryLotContribution,
    PantryShortageWarning,
)
from nourish_nest.models import PantryItem, PantryItemStatus, utc_now
from nourish_nest.pantry_repositories import PantryRepository


class GroceryShortageService:
    def __init__(self, session: Session):
        self.session = session
        self.requirements = GroceryRequirementsService(session)
        self.pantry = PantryRepository(session)

    def preview(
        self, household_id: uuid.UUID, data: GroceryRequirementsRequest
    ) -> GroceryShortageResponse:
        calculation_as_of = utc_now()
        with self.session.no_autoflush, localcontext() as context:
            context.prec = 28
            context.rounding = "ROUND_HALF_EVEN"
            preview = self.requirements.preview(household_id, data)
            warnings: list[RequirementWarning | PantryShortageWarning] = list(preview.warnings)
            required_foods = {row.food_id for row in preview.requirements}
            usable: dict[uuid.UUID, list[PantryItem]] = {}
            # Repository reads only: PantryService reads can mark lots expired and commit.
            for lot in sorted(self.pantry.items(household_id), key=lambda row: row.id):
                if lot.food_id not in required_foods or lot.quantity <= 0:
                    continue
                if lot.status not in {PantryItemStatus.ACTIVE, PantryItemStatus.EXPIRED}:
                    continue
                if lot.status == PantryItemStatus.EXPIRED or (
                    lot.expiration_date is not None and lot.expiration_date < calculation_as_of.date()
                ):
                    warnings.append(PantryShortageWarning(
                        code="excluded_expired_lot", message="Expired pantry lot excluded from availability.",
                        food_id=lot.food_id, pantry_item_id=lot.id, original_unit=lot.unit,
                    ))
                    continue
                usable.setdefault(lot.food_id, []).append(lot)
            shortages = []
            for requirement in preview.requirements:
                available = Decimal(0)
                contributions = []
                for lot in usable.get(requirement.food_id, []):
                    warning_fields = {
                        "food_id": lot.food_id, "pantry_item_id": lot.id, "original_unit": lot.unit,
                        "requirement_canonical_unit": requirement.canonical_unit,
                    }
                    try:
                        converted = convert_quantity(lot.quantity, lot.unit)
                    except UnsupportedConversionError as exc:
                        warnings.append(PantryShortageWarning(
                            code="unsupported_pantry_conversion", message=str(exc), **warning_fields,
                        ))
                        continue
                    if converted.canonical_unit != requirement.canonical_unit:
                        warnings.append(PantryShortageWarning(
                            code="incompatible_pantry_units",
                            message="Pantry lot excluded for this dimension; no density or count conversion was inferred.",
                            pantry_canonical_unit=converted.canonical_unit, **warning_fields,
                        ))
                        continue
                    available += converted.quantity
                    contributions.append(PantryLotContribution(
                        pantry_item_id=lot.id, quantity=lot.quantity, original_unit=lot.unit,
                        available_quantity=converted.quantity, expiration_date=lot.expiration_date,
                    ))
                shortage = max(requirement.required_quantity - available, Decimal(0))
                shortages.append(GroceryShortage(
                    **requirement.model_dump(), available_quantity=available,
                    shortage_quantity=shortage, purchase_required=shortage > 0,
                    pantry_lots=contributions,
                ))
            return GroceryShortageResponse(
                household_id=household_id, recipes=preview.recipes, requirements=shortages,
                warnings=warnings, calculation_as_of=calculation_as_of,
            )
