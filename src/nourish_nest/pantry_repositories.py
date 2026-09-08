import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from nourish_nest.models import (
    PantryItem,
    PantryItemStatus,
    PantryLocation,
    PantryStockRule,
    PantryTransaction,
    PantryTransactionType,
)


class PantryRepository:
    def __init__(self, session: Session):
        self.session = session

    def location(self, household_id: uuid.UUID, location_id: uuid.UUID) -> PantryLocation | None:
        return self.session.scalar(
            select(PantryLocation).where(
                PantryLocation.household_id == household_id, PantryLocation.id == location_id
            )
        )

    def locations(self, household_id: uuid.UUID) -> list[PantryLocation]:
        return list(
            self.session.scalars(
                select(PantryLocation)
                .where(PantryLocation.household_id == household_id)
                .order_by(PantryLocation.name)
            ).all()
        )

    def item(self, household_id: uuid.UUID, item_id: uuid.UUID, for_update: bool = False) -> PantryItem | None:
        statement = (
            select(PantryItem)
            .options(selectinload(PantryItem.food), selectinload(PantryItem.location))
            .where(PantryItem.household_id == household_id, PantryItem.id == item_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def items(
        self,
        household_id: uuid.UUID,
        statuses: set[PantryItemStatus] | None = None,
        for_update: bool = False,
    ) -> list[PantryItem]:
        statement = (
            select(PantryItem)
            .options(selectinload(PantryItem.food), selectinload(PantryItem.location))
            .where(PantryItem.household_id == household_id)
            .order_by(PantryItem.expiration_date.is_(None), PantryItem.expiration_date, PantryItem.created_at)
        )
        if statuses:
            statement = statement.where(PantryItem.status.in_(statuses))
        if for_update:
            statement = statement.with_for_update()
        return list(self.session.scalars(statement).all())

    def expiring(self, household_id: uuid.UUID, through: date, expired: bool = False) -> list[PantryItem]:
        today = datetime.now(UTC).date()
        statement = (
            select(PantryItem)
            .options(selectinload(PantryItem.food), selectinload(PantryItem.location))
            .where(
                PantryItem.household_id == household_id,
                PantryItem.expiration_date.is_not(None),
                PantryItem.status == (PantryItemStatus.EXPIRED if expired else PantryItemStatus.ACTIVE),
            )
        )
        if expired:
            statement = statement.where(PantryItem.expiration_date < today)
        else:
            statement = statement.where(
                PantryItem.expiration_date >= today, PantryItem.expiration_date <= through
            )
        return list(self.session.scalars(statement.order_by(PantryItem.expiration_date)).all())

    def stock_rule(self, household_id: uuid.UUID, food_id: uuid.UUID) -> PantryStockRule | None:
        return self.session.scalar(
            select(PantryStockRule).where(
                PantryStockRule.household_id == household_id, PantryStockRule.food_id == food_id
            )
        )

    def stock_rules(self, household_id: uuid.UUID) -> list[PantryStockRule]:
        return list(
            self.session.scalars(
                select(PantryStockRule)
                .where(PantryStockRule.household_id == household_id)
                .order_by(PantryStockRule.created_at)
            ).all()
        )

    def transaction_by_key(
        self, household_id: uuid.UUID, transaction_type: PantryTransactionType, key: str
    ) -> PantryTransaction | None:
        return self.session.scalar(
            select(PantryTransaction).where(
                PantryTransaction.household_id == household_id,
                PantryTransaction.transaction_type == transaction_type,
                PantryTransaction.idempotency_key == key,
            )
        )