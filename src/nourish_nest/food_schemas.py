import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nourish_nest.models import (
    FoodAllergenRelationship,
    FoodDietaryTag,
    FoodSourceType,
)


class FoodAllergenInput(BaseModel):
    allergen: str = Field(min_length=1, max_length=100)
    relationship_type: FoodAllergenRelationship


class FoodAllergenResponse(FoodAllergenInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class FoodDietaryTagInput(BaseModel):
    tag: FoodDietaryTag


class FoodDietaryTagResponse(FoodDietaryTagInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class FoodFields(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    source_type: FoodSourceType = FoodSourceType.MANUAL
    external_source_identifier: str | None = Field(default=None, max_length=200)
    source_provider: str | None = Field(default=None, max_length=64)
    serving_quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    serving_unit: str = Field(min_length=1, max_length=32)
    grams_per_serving: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
    calories_per_serving: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    protein_g: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    carbohydrate_g: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    fat_g: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    fiber_g: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    sugar_g: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    sodium_mg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    allergens: list[FoodAllergenInput] = Field(default_factory=list)
    dietary_tags: list[FoodDietaryTagInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_provenance(self) -> "FoodFields":
        if self.source_type == FoodSourceType.EXTERNAL and (
            not self.source_provider or not self.external_source_identifier
        ):
            raise ValueError(
                "source_provider and external_source_identifier are required for external foods"
            )
        return self


class FoodCreate(FoodFields):
    pass


class FoodUpdate(FoodFields):
    pass


class FoodResponse(FoodFields):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    normalized_name: str
    source_provider: str | None = None
    created_at: datetime
    updated_at: datetime
    source_data_type: str | None = None
    source_attribution: str | None = None
    source_retrieved_at: datetime | None = None
    allergens: list[FoodAllergenResponse] = Field(default_factory=list)
    dietary_tags: list[FoodDietaryTagResponse] = Field(default_factory=list)


class FoodSearchResponse(BaseModel):
    foods: list[FoodResponse]


class RecipeIngredientInput(BaseModel):
    food_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit: str = Field(min_length=1, max_length=32)
    preparation_note: str | None = Field(default=None, max_length=300)
    display_order: int = Field(ge=0)


class RecipeIngredientResponse(RecipeIngredientInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    food: FoodResponse


class RecipeInstructionInput(BaseModel):
    step_number: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=4000)


class RecipeInstructionResponse(RecipeInstructionInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class RecipeFields(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    cuisine: str | None = Field(default=None, max_length=100)
    preparation_minutes: int = Field(default=0, ge=0, le=10000)
    cooking_minutes: int = Field(default=0, ge=0, le=10000)
    servings: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    source: str | None = Field(default=None, max_length=200)
    ingredients: list[RecipeIngredientInput] = Field(min_length=1)
    instructions: list[RecipeInstructionInput] = Field(default_factory=list)


class RecipeCreate(RecipeFields):
    pass


class RecipeUpdate(RecipeFields):
    pass


class RecipeResponse(RecipeFields):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    household_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    ingredients: list[RecipeIngredientResponse]
    instructions: list[RecipeInstructionResponse]


class RecipeListResponse(BaseModel):
    recipes: list[RecipeResponse]


class RecipeNutritionMacros(BaseModel):
    protein_g: Decimal | None
    carbohydrate_g: Decimal | None
    fat_g: Decimal | None
    fiber_g: Decimal | None
    sugar_g: Decimal | None
    sodium_mg: Decimal | None


class RecipeNutritionResponse(BaseModel):
    total_calories: Decimal
    total_macros: RecipeNutritionMacros
    calories_per_serving: Decimal
    macros_per_serving: RecipeNutritionMacros
    aggregated_allergens: list[str]
    dietary_tags: list[FoodDietaryTag]
    warnings: list[str]
    calculation_version: str = "recipe-nutrition-v1"