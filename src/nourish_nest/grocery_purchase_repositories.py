import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from nourish_nest.models import GroceryList, GroceryListItem, GroceryPurchaseEvent


class GroceryPurchaseRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_list(self, household_id: uuid.UUID, list_id: uuid.UUID):
        return self.session.scalar(select(GroceryList).where(
            GroceryList.household_id == household_id, GroceryList.id == list_id
        ).with_for_update().execution_options(populate_existing=True))

    def get_item(self, household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID):
        return self.session.scalar(select(GroceryListItem).join(GroceryList).where(
            GroceryList.household_id == household_id, GroceryListItem.grocery_list_id == list_id,
            GroceryListItem.id == item_id,
        ).execution_options(populate_existing=True))

    def event(self, household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID, key: str):
        return self.session.scalar(select(GroceryPurchaseEvent).where(
            GroceryPurchaseEvent.household_id == household_id,
            GroceryPurchaseEvent.grocery_list_id == list_id,
            GroceryPurchaseEvent.grocery_list_item_id == item_id,
            GroceryPurchaseEvent.idempotency_key == key,
        ))

    def other_unchecked(self, list_id: uuid.UUID, item_id: uuid.UUID) -> bool:
        return self.session.scalar(select(GroceryListItem.id).where(
            GroceryListItem.grocery_list_id == list_id, GroceryListItem.id != item_id,
            GroceryListItem.checked.is_(False),
        ).limit(1)) is not None
