"""HTTP wire contracts shared by the deterministic planner and its HTTP client."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    member_id: UUID | None = None
    maximum_missing_ingredients: int = Field(default=3, ge=0, le=100, strict=True)
    maximum_cooking_minutes: int | None = Field(default=None, ge=1, le=1440, strict=True)
    cuisine: str | None = Field(default=None, min_length=1, max_length=100)
    limit: int = Field(default=10, ge=1, le=50, strict=True)


class PlanningWarning(BaseModel):
    code: str
    message: str


class FoodNeed(BaseModel):
    food_id: UUID
    food_name: str
    required_quantity: Decimal
    available_quantity: Decimal
    missing_quantity: Decimal
    unit: str
    conversion_supported: bool = True


class ExpiringFood(BaseModel):
    food_id: UUID
    food_name: str
    quantity: Decimal
    unit: str
    earliest_expiration: date


class ServingNutrition(BaseModel):
    calories: Decimal | None = None
    protein_g: Decimal | None = None
    carbohydrate_g: Decimal | None = None
    fat_g: Decimal | None = None
    warnings: list[str] = Field(default_factory=list)


class RecommendationScore(BaseModel):
    coverage_points: Decimal
    expiring_points: Decimal
    total: Decimal


class RecipeRecommendation(BaseModel):
    recipe_id: UUID
    recipe_name: str
    system_recipe: bool
    servings: Decimal
    cuisine: str | None
    preparation_minutes: int
    cooking_minutes: int
    coverage_percentage: Decimal
    classification: Literal["Ready to make", "Missing 1–2 ingredients", "Needs shopping"]
    missing_ingredient_count: int
    requirements: list[FoodNeed]
    missing_ingredients: list[FoodNeed]
    expiring_ingredients: list[ExpiringFood]
    score: RecommendationScore
    explanation: str
    nutrition_per_serving: ServingNutrition
    warnings: list[PlanningWarning]


class RecommendationResponse(BaseModel):
    household_id: UUID
    member_id: UUID | None
    recommendations: list[RecipeRecommendation]
    warnings: list[PlanningWarning]
    calculation_as_of: datetime
    calculation_version: Literal["recipe-recommendations-v1"] = "recipe-recommendations-v1"
