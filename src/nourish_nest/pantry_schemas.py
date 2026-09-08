import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from nourish_nest.models import PantryItemStatus, PantryLocationType, PantryTransactionType


class PantryLocationFields(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    location_type: PantryLocationType


class PantryLocationCreate(PantryLocationFields):
    pass


class PantryLocationResponse(PantryLocationFields):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    household_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PantryItemFields(BaseModel):
    location_id: uuid.UUID
    food_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit: str = Field(min_length=1, max_length=32)
    purchase_date: date | None = None
    opened_date: date | None = None
    expiration_date: date | None = None
    best_before_date: date | None = None
    lot_note: str | None = Field(default=None, max_length=300)


class PantryItemCreate(PantryItemFields):
    pass


class PantryItemUpdate(PantryItemFields):
    version: int = Field(ge=1)


class PantryItemResponse(PantryItemFields):
    model_config = ConfigDict(from_attributes=True)

    quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    id: uuid.UUID
    household_id: uuid.UUID
    canonical_quantity: Decimal | None
    canonical_unit: str | None
    status: PantryItemStatus
    version: int
    created_at: datetime
    updated_at: datetime


class PantryAdjustment(BaseModel):
    quantity_change: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit: str = Field(min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=300)
    idempotency_key: str | None = Field(default=None, max_length=200)
    version: int = Field(ge=1)


class PantryDiscard(PantryAdjustment):
    pass


class PantryConsumeRequest(BaseModel):
    food_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit: str = Field(min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=300)
    idempotency_key: str | None = Field(default=None, max_length=200)


class PantryTransferRequest(BaseModel):
    item_id: uuid.UUID
    target_location_id: uuid.UUID
    idempotency_key: str | None = Field(default=None, max_length=200)
    version: int = Field(ge=1)


class PantryTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pantry_item_id: uuid.UUID
    transaction_type: PantryTransactionType
    quantity_change: Decimal
    unit: str
    canonical_quantity_change: Decimal | None
    reason: str | None
    idempotency_key: str | None
    created_at: datetime


class PantryStockRuleFields(BaseModel):
    threshold_quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    threshold_unit: str = Field(min_length=1, max_length=32)
    preferred_reorder_quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    preferred_reorder_unit: str = Field(min_length=1, max_length=32)


class PantryStockRuleResponse(PantryStockRuleFields):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    household_id: uuid.UUID
    food_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PantrySummaryResponse(BaseModel):
    active_items: int
    depleted_items: int
    expired_items: int
    discarded_items: int
    low_stock_food_ids: list[uuid.UUID]