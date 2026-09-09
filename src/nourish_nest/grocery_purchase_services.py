import hashlib
import json
import uuid
from datetime import UTC
from decimal import Decimal, localcontext

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from nourish_nest.food_services import convert_quantity, normalize_unit
from nourish_nest.grocery_purchase_repositories import GroceryPurchaseRepository
from nourish_nest.grocery_purchase_schemas import GroceryPurchaseRequest, GroceryPurchaseResponse
from nourish_nest.grocery_services import StaleGroceryVersionError
from nourish_nest.models import (
    GroceryListStatus,
    GroceryPurchaseEvent,
    PantryItem,
    PantryItemStatus,
    PantryTransaction,
    PantryTransactionType,
    utc_now,
)
from nourish_nest.pantry_repositories import PantryRepository
from nourish_nest.repositories import HouseholdRepository
from nourish_nest.services import NotFoundError


class GroceryPurchaseError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def purchase_request_hash(data: GroceryPurchaseRequest) -> str:
    canonical = data.model_dump(mode="json", exclude={"idempotency_key"})
    canonical["purchased_unit"] = normalize_unit(data.purchased_unit)
    with localcontext() as context:
        context.prec = 28
        for key in ("purchased_quantity", "purchase_price"):
            value = getattr(data, key)
            if value is not None:
                canonical[key] = format(value.normalize(), "f")
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def exact_quantity(value: Decimal) -> Decimal:
    if (not value.is_finite() or value < 0 or value >= Decimal(10**12)
            or value != value.quantize(Decimal("0.000001"))):
        raise GroceryPurchaseError("invalid_request", "Purchase quantities must fit NUMERIC(18,6) exactly")
    return value


class GroceryPurchaseService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = GroceryPurchaseRepository(session)

    @staticmethod
    def _response(event, replayed=False):
        response = GroceryPurchaseResponse.model_validate(event)
        if response.created_at.tzinfo is None:
            response.created_at = response.created_at.replace(tzinfo=UTC)
        response.replayed = replayed
        return response

    def _replay(self, household_id, list_id, item_id, data, request_hash):
        event = self.repo.event(household_id, list_id, item_id, data.idempotency_key)
        if event is None:
            return None
        if event.request_hash != request_hash:
            raise GroceryPurchaseError("idempotency_conflict", "Idempotency key was used for a different purchase request")
        return self._response(event, True)

    def purchase(self, household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID, data: GroceryPurchaseRequest):
        request_hash = purchase_request_hash(data)
        for attempt in range(2):
            try:
                with self.session.no_autoflush, localcontext() as context:
                    context.prec = 28
                    context.rounding = "ROUND_HALF_EVEN"
                    return self._purchase(household_id, list_id, item_id, data, request_hash)
            except (IntegrityError, StaleDataError) as exc:
                self.session.rollback()
                try:
                    replay = self._replay(household_id, list_id, item_id, data, request_hash)
                    if replay is not None:
                        return replay
                finally:
                    self.session.rollback()
                if isinstance(exc, StaleDataError):
                    # Another item's purchase can change the parent version; retry once.
                    if attempt == 0:
                        continue
                    raise StaleGroceryVersionError("Grocery record version is stale") from exc
                raise
            except Exception:
                self.session.rollback()
                raise

    def _purchase(self, household_id, list_id, item_id, data, request_hash):
        household = HouseholdRepository(self.session).get(household_id)
        if household is None:
            raise NotFoundError("Household not found")
        listing = self.repo.get_list(household_id, list_id)
        if listing is None:
            raise NotFoundError("Grocery list not found")
        item = self.repo.get_item(household_id, list_id, item_id)
        if item is None:
            raise NotFoundError("Grocery item not found")
        replay = self._replay(household_id, list_id, item_id, data, request_hash)
        if replay is not None:
            self.session.rollback()
            return replay
        if item.version != data.expected_item_version:
            raise StaleGroceryVersionError("Grocery record version is stale")
        if data.pantry_location_id is not None and PantryRepository(self.session).location(
            household_id, data.pantry_location_id
        ) is None:
            raise NotFoundError("Pantry location not found")
        if data.add_to_pantry and item.food_id is None:
            raise GroceryPurchaseError("missing_food_reference", "A food reference is required for pantry intake")
        converted = convert_quantity(data.purchased_quantity, data.purchased_unit)
        item_factor = convert_quantity(Decimal(1), item.required_unit)
        if converted.canonical_unit != item_factor.canonical_unit:
            raise GroceryPurchaseError("incompatible_units", "Purchase and grocery item units have incompatible dimensions")
        delta = exact_quantity(converted.quantity / item_factor.quantity)
        if delta <= 0:
            raise GroceryPurchaseError("invalid_request", "Converted purchase quantity must be positive")
        total = exact_quantity(item.purchased_quantity + delta)
        if total > item.required_quantity and not data.allow_overpurchase:
            raise GroceryPurchaseError("overpurchase_not_allowed", "Purchase would exceed the required quantity")
        now = utc_now()
        item.purchased_quantity = total
        item.checked = total >= item.required_quantity
        item.version += 1
        item.updated_at = now
        listing.version += 1
        listing.updated_at = now
        if item.checked and not self.repo.other_unchecked(list_id, item_id):
            listing.status = GroceryListStatus.COMPLETED
        self.session.flush()  # Version-qualified item/list updates precede intake writes.
        event = GroceryPurchaseEvent(
            id=uuid.uuid4(), household_id=household_id, grocery_list_id=list_id, grocery_list_item_id=item_id,
            idempotency_key=data.idempotency_key, request_hash=request_hash,
            purchased_quantity=data.purchased_quantity, purchased_unit=normalize_unit(data.purchased_unit),
            item_quantity=delta, item_unit=item.required_unit, purchased_total=total,
            checked=item.checked, item_version=item.version, grocery_list_version=listing.version,
            grocery_list_status=listing.status, add_to_pantry=data.add_to_pantry,
            allow_overpurchase=data.allow_overpurchase, pantry_location_id=data.pantry_location_id,
            expiration_date=data.expiration_date, purchase_price=data.purchase_price,
            currency=household.currency, created_at=now,
        )
        if data.add_to_pantry:
            lot = PantryItem(
                household_id=household_id, location_id=data.pantry_location_id, food_id=item.food_id,
                quantity=data.purchased_quantity, unit=normalize_unit(data.purchased_unit),
                canonical_quantity=exact_quantity(converted.quantity), canonical_unit=converted.canonical_unit,
                purchase_date=now.date(), expiration_date=data.expiration_date,
                status=PantryItemStatus.EXPIRED if data.expiration_date and data.expiration_date < now.date()
                else PantryItemStatus.ACTIVE,
            )
            transaction = PantryTransaction(
                household_id=household_id, pantry_item=lot, transaction_type=PantryTransactionType.RESTOCK,
                quantity_change=data.purchased_quantity, unit=lot.unit,
                canonical_quantity_change=lot.canonical_quantity,
                reason=f"Grocery purchase {event.id}", idempotency_key=f"grocery-purchase:{event.id}",
                created_at=now,
            )
            event.pantry_item = lot
            event.pantry_transaction = transaction
        self.session.add(event)
        self.session.flush()
        self.session.refresh(event)
        response = self._response(event)
        self.session.commit()
        return response
