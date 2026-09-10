"""Pantry HTTP contracts, independent of backend models and calculations."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Quantity = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
Key = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
Unit = Annotated[str, Field(min_length=1, max_length=32)]
LocationType = Literal["pantry", "refrigerator", "freezer", "cabinet", "custom"]


class LocationInput(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    location_type: LocationType


class LocationRecord(LocationInput):
    id: UUID
    household_id: UUID


class PantryItemInput(BaseModel):
    food_id: UUID
    location_id: UUID
    quantity: Quantity
    unit: Unit
    expiration_date: date | None = None
    purchase_date: date | None = None


class PantryLot(PantryItemInput):
    id: UUID
    household_id: UUID
    quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    status: Literal["active", "depleted", "expired", "discarded"]
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class PantryOverview(BaseModel):
    active_items: int = Field(ge=0)
    expired_items: int = Field(ge=0)
    depleted_items: int = Field(ge=0)
    discarded_items: int = Field(ge=0)
    low_stock_food_ids: list[UUID]


class AdjustmentInput(BaseModel):
    quantity_change: Quantity
    unit: Unit
    version: int = Field(ge=1)
    idempotency_key: Key
    reason: str | None = Field(default=None, max_length=300)


class ConsumeInput(BaseModel):
    food_id: UUID
    quantity: Quantity
    unit: Unit
    idempotency_key: Key
    reason: str | None = Field(default=None, max_length=300)


class TransferInput(BaseModel):
    item_id: UUID
    target_location_id: UUID
    version: int = Field(ge=1)
    idempotency_key: Key


class StockRuleInput(BaseModel):
    threshold_quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    threshold_unit: Unit
    preferred_reorder_quantity: Quantity
    preferred_reorder_unit: Unit


class StockRule(StockRuleInput):
    id: UUID
    household_id: UUID
    food_id: UUID
