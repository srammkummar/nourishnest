import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nourish_nest.grocery_schemas import Quantity
from nourish_nest.models import GroceryListStatus


class GroceryPurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    purchased_quantity: Quantity = Field(gt=0)
    purchased_unit: str = Field(min_length=1, max_length=32)
    expected_item_version: int = Field(ge=1, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=200)
    add_to_pantry: bool
    pantry_location_id: uuid.UUID | None = None
    expiration_date: date | None = None
    purchase_price: Quantity | None = None
    allow_overpurchase: bool = False

    @model_validator(mode="after")
    def intake_location(self) -> Self:
        if self.add_to_pantry and self.pantry_location_id is None:
            raise ValueError("pantry_location_id is required for pantry intake")
        return self


class GroceryPurchaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    purchase_event_id: uuid.UUID = Field(validation_alias="id")
    grocery_list_id: uuid.UUID
    grocery_list_item_id: uuid.UUID
    purchased_quantity: Decimal
    purchased_unit: str
    item_quantity: Decimal
    item_unit: str
    purchased_total: Decimal
    checked: bool
    item_version: int
    grocery_list_version: int
    grocery_list_status: GroceryListStatus
    add_to_pantry: bool
    allow_overpurchase: bool
    pantry_location_id: uuid.UUID | None
    pantry_item_id: uuid.UUID | None
    pantry_transaction_id: uuid.UUID | None
    expiration_date: date | None
    purchase_price: Decimal | None
    currency: str
    created_at: datetime
    replayed: bool = False
