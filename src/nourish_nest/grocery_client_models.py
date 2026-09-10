"""Typed grocery wire contracts. No ORM imports or stock calculations."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def decimal_input(value):
    if isinstance(value, (float, bool)):
        raise ValueError("Use decimal text or an integer for quantities")  # noqa: TRY004
    return value


Quantity = Annotated[
    Decimal,
    BeforeValidator(decimal_input),
    Field(ge=0, lt=Decimal(10**12), max_digits=18, decimal_places=6),
]
Status = Literal["draft", "active", "completed", "archived"]
Key = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]


class ListInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    status: Status = "draft"


class ListUpdate(ListInput):
    expected_version: int = Field(ge=1, strict=True)


class GroceryList(ListInput):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    household_id: UUID
    version: int


class ItemFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    food_id: UUID | None = None
    display_name: str = Field(min_length=1, max_length=200)
    required_quantity: Quantity
    required_unit: str = Field(min_length=1, max_length=32)
    purchased_quantity: Quantity = Decimal(0)
    category: str | None = Field(default=None, max_length=100)
    source_type: Literal["manual", "recipe", "low_stock"] = "manual"
    source_reference_id: UUID | None = None
    checked: bool = False


class ItemInput(ItemFields):
    @model_validator(mode="after")
    def valid_purchase(self):
        if self.purchased_quantity > self.required_quantity:
            raise ValueError("Required quantity cannot be below the existing purchased quantity")
        if self.checked and self.purchased_quantity != self.required_quantity:
            raise ValueError("A checked item's required quantity must equal its purchased quantity")
        return self


class ItemUpdate(ItemInput):
    expected_version: int = Field(ge=1, strict=True)


class GroceryItem(ItemFields):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    grocery_list_id: UUID
    version: int


class RecipeSelection(BaseModel):
    recipe_id: UUID
    desired_servings: Quantity = Field(gt=0)


class RequirementsInput(BaseModel):
    recipes: list[RecipeSelection] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_recipes(self):
        if len({r.recipe_id for r in self.recipes}) != len(self.recipes):
            raise ValueError("Select each recipe only once")
        return self


class Source(BaseModel):
    recipe_id: UUID
    recipe_name: str
    ingredient_id: UUID
    scaled_quantity: Decimal
    original_unit: str
    required_quantity: Decimal


class WarningRecord(BaseModel):
    code: str
    message: str
    food_id: UUID
    recipe_id: UUID | None = None
    ingredient_id: UUID | None = None
    pantry_item_id: UUID | None = None
    original_unit: str | None = None
    scaled_quantity: Decimal | None = None
    canonical_units: list[str] = Field(default_factory=list)
    requirement_canonical_unit: str | None = None
    pantry_canonical_unit: str | None = None


class Requirement(BaseModel):
    food_id: UUID
    food_name: str
    required_quantity: Decimal
    canonical_unit: str
    sources: list[Source]


class RequirementsResult(RequirementsInput):
    household_id: UUID
    requirements: list[Requirement]
    warnings: list[WarningRecord]
    calculation_version: str


class PantrySource(BaseModel):
    pantry_item_id: UUID
    quantity: Decimal
    original_unit: str
    available_quantity: Decimal
    expiration_date: date | None


class Shortage(Requirement):
    available_quantity: Decimal
    shortage_quantity: Decimal
    purchase_required: bool
    pantry_lots: list[PantrySource]


class ShortageResult(RequirementsResult):
    requirements: list[Shortage]
    calculation_as_of: datetime


class GenerationInput(RequirementsInput):
    expected_list_version: int = Field(ge=1, strict=True)
    idempotency_key: Key


class RecipeLineage(BaseModel):
    id: UUID
    recipe_id: UUID
    recipe_ingredient_id: UUID | None
    required_quantity: Decimal
    canonical_unit: str


class GeneratedItem(GroceryItem):
    generation_run_id: UUID
    recipe_sources: list[RecipeLineage]


class GenerationResult(BaseModel):
    generation_run_id: UUID
    grocery_list_id: UUID
    grocery_list_version: int
    replayed: bool
    created_items: list[GeneratedItem]
    warnings: list[WarningRecord]
    warnings_available: bool
    calculation_as_of: datetime
    calculation_version: str


class PurchaseInput(BaseModel):
    purchased_quantity: Quantity = Field(gt=0)
    purchased_unit: str = Field(min_length=1, max_length=32)
    expected_item_version: int = Field(ge=1, strict=True)
    idempotency_key: Key
    add_to_pantry: bool
    pantry_location_id: UUID | None = None
    expiration_date: date | None = None
    purchase_price: Quantity | None = None
    allow_overpurchase: bool = False

    @model_validator(mode="after")
    def intake_location(self):
        if self.add_to_pantry and self.pantry_location_id is None:
            raise ValueError("Select a pantry location for intake")
        return self


class PurchaseResult(BaseModel):
    purchase_event_id: UUID
    grocery_list_id: UUID
    grocery_list_item_id: UUID
    purchased_quantity: Decimal
    purchased_unit: str
    item_quantity: Decimal
    item_unit: str
    purchased_total: Decimal
    checked: bool
    item_version: int
    grocery_list_version: int
    grocery_list_status: Status
    add_to_pantry: bool
    allow_overpurchase: bool
    pantry_location_id: UUID | None
    pantry_item_id: UUID | None
    pantry_transaction_id: UUID | None
    expiration_date: date | None
    purchase_price: Decimal | None
    currency: str
    created_at: datetime
    replayed: bool
