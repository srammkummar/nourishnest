import uuid
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nourish_nest.grocery_schemas import Quantity


class RecipeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: uuid.UUID
    desired_servings: Quantity = Field(gt=0)


class GroceryRequirementsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipes: list[RecipeSelection] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_recipes(self) -> Self:
        if len({selection.recipe_id for selection in self.recipes}) != len(self.recipes):
            raise ValueError("Duplicate recipe_id entries are not allowed")
        return self


class RecipeContribution(BaseModel):
    recipe_id: uuid.UUID
    recipe_name: str
    ingredient_id: uuid.UUID
    scaled_quantity: Decimal
    original_unit: str
    required_quantity: Decimal


class GroceryRequirement(BaseModel):
    food_id: uuid.UUID
    food_name: str
    required_quantity: Decimal
    canonical_unit: str
    sources: list[RecipeContribution]


class RequirementWarning(BaseModel):
    code: Literal["unsupported_conversion", "incompatible_units"]
    message: str
    food_id: uuid.UUID
    recipe_id: uuid.UUID | None = None
    ingredient_id: uuid.UUID | None = None
    scaled_quantity: Decimal | None = None
    original_unit: str | None = None
    canonical_units: list[str] = Field(default_factory=list)


class GroceryRequirementsResponse(BaseModel):
    household_id: uuid.UUID
    recipes: list[RecipeSelection]
    requirements: list[GroceryRequirement]
    warnings: list[RequirementWarning]
    calculation_version: Literal["grocery-requirements-v1"] = "grocery-requirements-v1"
