import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from nourish_nest.food_services import UnsupportedConversionError, convert_quantity
from nourish_nest.models import (
    Food,
    PantryItem,
    PantryItemStatus,
    PantryLocation,
    PantryStockRule,
    PantryTransaction,
    PantryTransactionType,
)
from nourish_nest.pantry_repositories import PantryRepository
from nourish_nest.pantry_schemas import (
    PantryAdjustment,
    PantryConsumeRequest,
    PantryItemCreate,
    PantryItemUpdate,
    PantryStockRuleFields,
    PantryTransferRequest,
)
from nourish_nest.repositories import HouseholdRepository


class PantryError(RuntimeError):
    code = "pantry_error"


class InsufficientInventoryError(PantryError):
    code = "insufficient_inventory"


class IncompatibleUnitsError(PantryError):
    code = "incompatible_units"


class PantryUnsupportedConversionError(PantryError):
    code = "unsupported_conversion"


class StaleInventoryVersionError(PantryError):
    code = "stale_inventory_version"


class DuplicateIdempotencyKeyError(PantryError):
    code = "duplicate_idempotency_key"


class PantryLocationNotEmptyError(PantryError):
    code = "pantry_location_not_empty"


class ExpiredInventoryError(PantryError):
    code = "expired_inventory"


class InvalidTransferError(PantryError):
    code = "invalid_transfer"


def _canonical(quantity: Decimal, unit: str):
    try:
        return convert_quantity(quantity, unit)
    except UnsupportedConversionError as exc:
        raise PantryUnsupportedConversionError(str(exc)) from exc


def _compatible_change(item: PantryItem, quantity: Decimal, unit: str) -> Decimal:
    try:
        requested = convert_quantity(quantity, unit)
        current = convert_quantity(item.quantity, item.unit)
    except UnsupportedConversionError as exc:
        raise PantryUnsupportedConversionError(str(exc)) from exc
    if requested.canonical_unit != current.canonical_unit:
        raise IncompatibleUnitsError(
            f"Cannot combine {unit} with pantry item unit {item.unit}"
        )
    return requested.quantity


class PantryService:
    def __init__(self, session: Session, expiring_soon_days: int = 3):
        self.session = session
        self.repo = PantryRepository(session)
        self.households = HouseholdRepository(session)
        self.expiring_soon_days = expiring_soon_days

    def _household(self, household_id: uuid.UUID):
        household = self.households.get(household_id)
        if household is None:
            raise LookupError("Household not found")
        return household

    def _food(self, food_id: uuid.UUID) -> Food:
        food = self.session.get(Food, food_id)
        if food is None:
            raise LookupError("Food not found")
        return food

    def _location(self, household_id: uuid.UUID, location_id: uuid.UUID) -> PantryLocation:
        location = self.repo.location(household_id, location_id)
        if location is None:
            raise LookupError("Pantry location not found")
        return location

    def _idempotency(
        self, household_id: uuid.UUID, transaction_type: PantryTransactionType, key: str | None
    ) -> None:
        if key and self.repo.transaction_by_key(household_id, transaction_type, key):
            raise DuplicateIdempotencyKeyError("Idempotency key has already been used")

    @staticmethod
    def _today() -> date:
        return datetime.now(UTC).date()

    def create_location(self, household_id: uuid.UUID, data):
        self._household(household_id)
        location = PantryLocation(household_id=household_id, **data.model_dump())
        self.session.add(location)
        self.session.commit()
        self.session.refresh(location)
        return location

    def list_locations(self, household_id: uuid.UUID):
        self._household(household_id)
        return self.repo.locations(household_id)

    def update_location(self, household_id: uuid.UUID, location_id: uuid.UUID, data):
        location = self._location(household_id, location_id)
        location.name = data.name
        location.location_type = data.location_type
        self.session.commit()
        return location

    def delete_location(self, household_id: uuid.UUID, location_id: uuid.UUID) -> None:
        location = self._location(household_id, location_id)
        if self.session.scalar(select(PantryItem.id).where(PantryItem.location_id == location.id)):
            raise PantryLocationNotEmptyError("Pantry location is not empty")
        self.session.delete(location)
        self.session.commit()

    def create_item(self, household_id: uuid.UUID, data: PantryItemCreate):
        self._household(household_id)
        self._location(household_id, data.location_id)
        self._food(data.food_id)
        converted = _canonical(data.quantity, data.unit)
        item = PantryItem(
            household_id=household_id,
            **data.model_dump(),
            canonical_quantity=converted.quantity,
            canonical_unit=converted.canonical_unit,
        )
        self.session.add(item)
        self.session.flush()
        self.session.add(
            PantryTransaction(
                household_id=item.household_id,
                pantry_item_id=item.id,
                transaction_type=PantryTransactionType.RESTOCK,
                quantity_change=data.quantity,
                unit=data.unit,
                canonical_quantity_change=converted.quantity,
                reason="Initial pantry item",
            )
        )
        self.session.commit()
        return self.repo.item(household_id, item.id)

    def list_items(self, household_id: uuid.UUID):
        self._household(household_id)
        self._mark_expired(household_id)
        return self.repo.items(household_id)

    def get_item(self, household_id: uuid.UUID, item_id: uuid.UUID, for_update: bool = False):
        self._household(household_id)
        self._mark_expired(household_id)
        item = self.repo.item(household_id, item_id, for_update=for_update)
        if item is None:
            raise LookupError("Pantry item not found")
        return item

    def update_item(self, household_id: uuid.UUID, item_id: uuid.UUID, data: PantryItemUpdate):
        item = self.get_item(household_id, item_id, for_update=True)
        if item.version != data.version:
            raise StaleInventoryVersionError("Pantry item version is stale")
        self._location(household_id, data.location_id)
        self._food(data.food_id)
        converted = _canonical(data.quantity, data.unit)
        for key, value in data.model_dump(exclude={"version"}).items():
            setattr(item, key, value)
        item.canonical_quantity = converted.quantity
        item.canonical_unit = converted.canonical_unit
        self.session.commit()
        return self.repo.item(household_id, item_id)

    def adjust(self, household_id: uuid.UUID, item_id: uuid.UUID, data: PantryAdjustment):
        item = self.get_item(household_id, item_id, for_update=True)
        self._idempotency(household_id, PantryTransactionType.ADJUST, data.idempotency_key)
        if item.version != data.version:
            raise StaleInventoryVersionError("Pantry item version is stale")
        change = _compatible_change(item, data.quantity_change, data.unit)
        if change == 0:
            raise ValueError("quantity_change cannot be zero")
        if item.status != PantryItemStatus.ACTIVE:
            raise ExpiredInventoryError("Only active pantry items can be adjusted")
        current = convert_quantity(item.quantity, item.unit).quantity
        if current + change < 0:
            raise InsufficientInventoryError("Adjustment would make inventory negative")
        self._apply_change(item, change)
        self._transaction(item, PantryTransactionType.ADJUST, data.quantity_change, data.unit, change, data.reason, data.idempotency_key)
        self._commit(household_id, PantryTransactionType.ADJUST, data.idempotency_key)
        return self.repo.item(household_id, item_id)

    def discard(self, household_id: uuid.UUID, item_id: uuid.UUID, data: PantryAdjustment):
        item = self.get_item(household_id, item_id, for_update=True)
        self._idempotency(household_id, PantryTransactionType.DISCARD, data.idempotency_key)
        if item.version != data.version:
            raise StaleInventoryVersionError("Pantry item version is stale")
        change = _compatible_change(item, data.quantity_change, data.unit)
        current = convert_quantity(item.quantity, item.unit).quantity
        if change > current:
            raise InsufficientInventoryError("Cannot discard more than available inventory")
        self._apply_change(item, -change)
        item.status = PantryItemStatus.DISCARDED
        self._transaction(item, PantryTransactionType.DISCARD, data.quantity_change, data.unit, -change, data.reason, data.idempotency_key)
        self._commit(household_id, PantryTransactionType.DISCARD, data.idempotency_key)
        return self.repo.item(household_id, item_id)

    def consume(self, household_id: uuid.UUID, data: PantryConsumeRequest):
        self._household(household_id)
        self._idempotency(household_id, PantryTransactionType.CONSUME, data.idempotency_key)
        self._mark_expired(household_id)
        requested = _canonical(data.quantity, data.unit)
        remaining = requested.quantity
        candidates = [
            item for item in self.repo.items(household_id, {PantryItemStatus.ACTIVE}, for_update=True)
            if item.food_id == data.food_id
        ]
        consumed: list[PantryItem] = []
        for item in candidates:
            if item.expiration_date and item.expiration_date < self._today():
                continue
            try:
                available = convert_quantity(item.quantity, item.unit)
            except UnsupportedConversionError as exc:
                raise PantryUnsupportedConversionError(str(exc)) from exc
            if available.canonical_unit != requested.canonical_unit:
                raise IncompatibleUnitsError(
                    f"Cannot combine {data.unit} with pantry item unit {item.unit}"
                )
            take = min(remaining, available.quantity)
            self._apply_change(item, -take)
            self._transaction(item, PantryTransactionType.CONSUME, -take, available.canonical_unit, -take, data.reason, data.idempotency_key if not consumed else None)
            consumed.append(item)
            remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            self.session.rollback()
            if not consumed and any(
                item.food_id == data.food_id and item.status == PantryItemStatus.EXPIRED
                for item in self.repo.items(household_id)
            ):
                raise ExpiredInventoryError("Expired pantry inventory cannot be consumed")
            raise InsufficientInventoryError("Insufficient compatible pantry inventory")
        self._commit(household_id, PantryTransactionType.CONSUME, data.idempotency_key)
        return [self.repo.item(household_id, item.id) for item in consumed]

    def transfer(self, household_id: uuid.UUID, data: PantryTransferRequest):
        item = self.get_item(household_id, data.item_id, for_update=True)
        target = self._location(household_id, data.target_location_id)
        self._idempotency(household_id, PantryTransactionType.TRANSFER, data.idempotency_key)
        if item.version != data.version:
            raise StaleInventoryVersionError("Pantry item version is stale")
        if item.location_id == target.id:
            raise InvalidTransferError("Source and target locations must differ")
        if item.status != PantryItemStatus.ACTIVE:
            raise ExpiredInventoryError("Only active pantry items can be transferred")
        old_location = item.location_id
        available = convert_quantity(item.quantity, item.unit)
        item.quantity = Decimal(0)
        item.canonical_quantity = Decimal(0)
        item.status = PantryItemStatus.DEPLETED
        destination = PantryItem(
            household_id=household_id,
            location_id=target.id,
            food_id=item.food_id,
            quantity=available.quantity,
            unit=available.canonical_unit,
            canonical_quantity=available.quantity,
            canonical_unit=available.canonical_unit,
            purchase_date=item.purchase_date,
            opened_date=item.opened_date,
            expiration_date=item.expiration_date,
            best_before_date=item.best_before_date,
            lot_note=item.lot_note,
            status=PantryItemStatus.ACTIVE,
        )
        self.session.add(destination)
        self.session.flush()
        self._transaction(
            item,
            PantryTransactionType.TRANSFER,
            -available.quantity,
            available.canonical_unit,
            -available.quantity,
            f"Transferred to {target.id}",
            data.idempotency_key,
        )
        self._transaction(
            destination,
            PantryTransactionType.TRANSFER,
            available.quantity,
            available.canonical_unit,
            available.quantity,
            f"Transferred from {old_location}",
            f"{data.idempotency_key}:destination" if data.idempotency_key else None,
        )
        self._commit(household_id, PantryTransactionType.TRANSFER, data.idempotency_key)
        return self.repo.item(household_id, destination.id)

    def expiring(self, household_id: uuid.UUID):
        self._household(household_id)
        self._mark_expired(household_id)
        through = self._today() + timedelta(days=self.expiring_soon_days)
        return self.repo.expiring(household_id, through)

    def expired(self, household_id: uuid.UUID):
        self._household(household_id)
        self._mark_expired(household_id)
        return self.repo.expiring(household_id, self._today(), expired=True)

    def low_stock(self, household_id: uuid.UUID):
        self._household(household_id)
        items = self.repo.items(household_id, {PantryItemStatus.ACTIVE})
        low: list[PantryStockRule] = []
        for rule in self.repo.stock_rules(household_id):
            try:
                threshold = convert_quantity(rule.threshold_quantity, rule.threshold_unit)
                available = sum(
                    (convert_quantity(item.quantity, item.unit).quantity for item in items if item.food_id == rule.food_id),
                    Decimal(0),
                )
            except UnsupportedConversionError:
                continue
            if threshold.canonical_unit == (convert_quantity(rule.threshold_quantity, rule.threshold_unit).canonical_unit) and available < threshold.quantity:
                low.append(rule)
        return low

    def upsert_stock_rule(self, household_id: uuid.UUID, food_id: uuid.UUID, data: PantryStockRuleFields):
        self._household(household_id)
        self._food(food_id)
        _canonical(data.threshold_quantity, data.threshold_unit)
        _canonical(data.preferred_reorder_quantity, data.preferred_reorder_unit)
        rule = self.repo.stock_rule(household_id, food_id)
        if rule is None:
            rule = PantryStockRule(household_id=household_id, food_id=food_id, **data.model_dump())
            self.session.add(rule)
        else:
            for key, value in data.model_dump().items():
                setattr(rule, key, value)
        self.session.commit()
        return rule

    def list_stock_rules(self, household_id: uuid.UUID):
        self._household(household_id)
        return self.repo.stock_rules(household_id)

    def delete_stock_rule(self, household_id: uuid.UUID, food_id: uuid.UUID):
        rule = self.repo.stock_rule(household_id, food_id)
        if rule is None:
            raise LookupError("Pantry stock rule not found")
        self.session.delete(rule)
        self.session.commit()

    def summary(self, household_id: uuid.UUID):
        items = self.list_items(household_id)
        counts = {status: sum(item.status == status for item in items) for status in PantryItemStatus}
        return counts, [rule.food_id for rule in self.low_stock(household_id)]

    def _mark_expired(self, household_id: uuid.UUID) -> None:
        today = self._today()
        for item in self.repo.items(household_id, {PantryItemStatus.ACTIVE}):
            if item.expiration_date and item.expiration_date < today:
                item.status = PantryItemStatus.EXPIRED
        self.session.commit()

    @staticmethod
    def _apply_change(item: PantryItem, canonical_change: Decimal) -> None:
        current = convert_quantity(item.quantity, item.unit)
        item.quantity += canonical_change / (current.quantity / item.quantity)
        item.canonical_quantity = current.quantity + canonical_change
        item.canonical_unit = current.canonical_unit
        if item.canonical_quantity <= 0:
            item.quantity = Decimal(0)
            item.canonical_quantity = Decimal(0)
            item.status = PantryItemStatus.DEPLETED

    def _transaction(self, item, transaction_type, quantity, unit, canonical, reason, key):
        self.session.add(
            PantryTransaction(
                household_id=item.household_id,
                pantry_item_id=item.id,
                transaction_type=transaction_type,
                quantity_change=quantity,
                unit=unit,
                canonical_quantity_change=canonical,
                reason=reason,
                idempotency_key=key,
            )
        )

    def _commit(
        self,
        household_id: uuid.UUID,
        transaction_type: PantryTransactionType,
        idempotency_key: str | None,
    ) -> None:
        try:
            self.session.commit()
        except StaleDataError as exc:
            self.session.rollback()
            raise StaleInventoryVersionError("Pantry item version is stale") from exc
        except IntegrityError as exc:
            self.session.rollback()
            if idempotency_key:
                raise DuplicateIdempotencyKeyError("Idempotency key has already been used") from exc
            raise