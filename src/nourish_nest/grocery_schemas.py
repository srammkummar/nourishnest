import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from nourish_nest.models import GroceryItemSourceType, GroceryListStatus


def _decimal_input(value):
    if isinstance(value, (float, bool)):
        # Pydantic converts ValueError to a 422; TypeError would escape validation.
        raise ValueError("Use a decimal string or integer for quantities")  # noqa: TRY004
    return value


Quantity = Annotated[
    Decimal, BeforeValidator(_decimal_input),
    Field(ge=0, lt=Decimal(10**12), max_digits=18, decimal_places=6),
]


class GroceryListCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    status: GroceryListStatus = GroceryListStatus.DRAFT


class GroceryListUpdate(GroceryListCreate):
    expected_version: int = Field(ge=1, strict=True)


class GroceryListResponse(GroceryListCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    household_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    version: int


class GroceryItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    food_id: uuid.UUID | None = None
    display_name: str = Field(min_length=1, max_length=200)
    required_quantity: Quantity
    required_unit: str = Field(min_length=1, max_length=32)
    purchased_quantity: Quantity = Decimal(0)
    category: str | None = Field(default=None, max_length=100)
    source_type: GroceryItemSourceType = GroceryItemSourceType.MANUAL
    source_reference_id: uuid.UUID | None = None
    checked: bool = False

    @model_validator(mode="after")
    def valid_purchase(self) -> Self:
        if self.purchased_quantity > self.required_quantity:
            raise ValueError("purchased_quantity cannot exceed required_quantity")
        if self.checked and self.purchased_quantity != self.required_quantity:
            raise ValueError("checked requires purchased_quantity to equal required_quantity")
        return self


class GroceryItemUpdate(GroceryItemCreate):
    expected_version: int = Field(ge=1, strict=True)


class GroceryItemResponse(GroceryItemCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grocery_list_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    version: int
