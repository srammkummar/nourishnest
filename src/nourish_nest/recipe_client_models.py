"""Recipe wire contracts, independent of backend schemas and ORM imports."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class StoredFood(BaseModel):
    id: UUID
    name: str
    brand: str | None = None


class StoredFoodSearch(BaseModel):
    foods: list[StoredFood]


class IngredientInput(BaseModel):
    food_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    unit: str = Field(min_length=1, max_length=32)
    preparation_note: str | None = Field(default=None, max_length=300)
    display_order: int = Field(ge=0)


class InstructionInput(BaseModel):
    step_number: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=4000)


class RecipeInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    cuisine: str | None = Field(default=None, max_length=100)
    preparation_minutes: int = Field(default=0, ge=0, le=10000)
    cooking_minutes: int = Field(default=0, ge=0, le=10000)
    servings: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    source: str | None = Field(default=None, max_length=200)
    ingredients: list[IngredientInput] = Field(min_length=1)
    instructions: list[InstructionInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_content(self):
        if not self.name.strip():
            raise ValueError("Enter a recipe name.")
        numbers = [step.step_number for step in self.instructions]
        if len(numbers) != len(set(numbers)):
            raise ValueError("Instruction numbers must be unique.")
        if any(not step.instruction.strip() for step in self.instructions):
            raise ValueError("Instruction steps cannot be blank; remove unused steps.")
        return self


class RecipeIngredient(IngredientInput):
    food: StoredFood


class RecipeRecord(RecipeInput):
    id: UUID
    household_id: UUID | None
    ingredients: list[RecipeIngredient]


class RecipeMacros(BaseModel):
    protein_g: Decimal | None
    carbohydrate_g: Decimal | None
    fat_g: Decimal | None
    fiber_g: Decimal | None
    sugar_g: Decimal | None
    sodium_mg: Decimal | None


class RecipeNutrition(BaseModel):
    total_calories: Decimal
    total_macros: RecipeMacros
    calories_per_serving: Decimal
    macros_per_serving: RecipeMacros
    aggregated_allergens: list[str]
    dietary_tags: list[str]
    warnings: list[str]
    calculation_version: str
