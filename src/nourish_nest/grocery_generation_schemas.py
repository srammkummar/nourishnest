import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nourish_nest.grocery_requirement_schemas import GroceryRequirementsRequest, RequirementWarning
from nourish_nest.grocery_schemas import GroceryItemResponse
from nourish_nest.grocery_shortage_schemas import PantryShortageWarning


class GroceryGenerationRequest(GroceryRequirementsRequest):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_list_version: int = Field(ge=1, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=200)


class GroceryRecipeSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipe_id: uuid.UUID
    recipe_ingredient_id: uuid.UUID | None
    required_quantity: Decimal
    canonical_unit: str
    created_at: datetime


class GeneratedGroceryItemResponse(GroceryItemResponse):
    generation_run_id: uuid.UUID
    recipe_sources: list[GroceryRecipeSourceResponse]


class GroceryGenerationResponse(BaseModel):
    generation_run_id: uuid.UUID
    grocery_list_id: uuid.UUID
    grocery_list_version: int
    replayed: bool
    created_items: list[GeneratedGroceryItemResponse]
    warnings: list[RequirementWarning | PantryShortageWarning]
    warnings_available: bool
    calculation_as_of: datetime
    calculation_version: Literal["grocery-generation-v1"] = "grocery-generation-v1"
