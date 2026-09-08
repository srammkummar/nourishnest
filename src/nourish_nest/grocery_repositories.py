import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from nourish_nest.models import GroceryList, GroceryListItem


class GroceryRepository:
    def __init__(self, session: Session):
        self.session = session

    def lists(self, household_id: uuid.UUID) -> list[GroceryList]:
        return list(self.session.scalars(
            select(GroceryList).where(GroceryList.household_id == household_id)
            .order_by(GroceryList.created_at, GroceryList.id)
        ))

    def get_list(self, household_id: uuid.UUID, list_id: uuid.UUID) -> GroceryList | None:
        return self.session.scalar(select(GroceryList).where(
            GroceryList.household_id == household_id, GroceryList.id == list_id
        ))

    def items(self, household_id: uuid.UUID, list_id: uuid.UUID) -> list[GroceryListItem]:
        return list(self.session.scalars(
            select(GroceryListItem).join(GroceryList).where(
                GroceryList.household_id == household_id, GroceryListItem.grocery_list_id == list_id
            ).order_by(GroceryListItem.created_at, GroceryListItem.id)
        ))

    def get_item(
        self, household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID
    ) -> GroceryListItem | None:
        return self.session.scalar(select(GroceryListItem).join(GroceryList).where(
            GroceryList.household_id == household_id,
            GroceryListItem.grocery_list_id == list_id, GroceryListItem.id == item_id,
        ))

    def add(self, record: GroceryList | GroceryListItem) -> None:
        self.session.add(record)

    def delete(self, record: GroceryList | GroceryListItem) -> None:
        self.session.delete(record)
