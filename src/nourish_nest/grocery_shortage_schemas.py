import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from nourish_nest.grocery_requirement_schemas import (
    GroceryRequirement,
    RecipeSelection,
    RequirementWarning,
)


class PantryLotContribution(BaseModel):
    pantry_item_id: uuid.UUID
    quantity: Decimal
    original_unit: str
    available_quantity: Decimal
    expiration_date: date | None


class GroceryShortage(GroceryRequirement):
    available_quantity: Decimal
    shortage_quantity: Decimal
    purchase_required: bool
    pantry_lots: list[PantryLotContribution]


class PantryShortageWarning(BaseModel):
    code: Literal["excluded_expired_lot", "incompatible_pantry_units", "unsupported_pantry_conversion"]
    message: str
    food_id: uuid.UUID
    pantry_item_id: uuid.UUID
    original_unit: str
    requirement_canonical_unit: str | None = None
    pantry_canonical_unit: str | None = None


class GroceryShortageResponse(BaseModel):
    household_id: uuid.UUID
    recipes: list[RecipeSelection]
    requirements: list[GroceryShortage]
    warnings: list[RequirementWarning | PantryShortageWarning]
    calculation_as_of: datetime
    calculation_version: Literal["grocery-shortage-v1"] = "grocery-shortage-v1"
