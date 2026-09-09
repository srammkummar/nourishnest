import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from nourish_nest.models import GroceryGenerationRun, GroceryList, GroceryListItem


class GroceryGenerationRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_list(self, household_id: uuid.UUID, list_id: uuid.UUID, lock: bool = False):
        statement = select(GroceryList).where(
            GroceryList.household_id == household_id, GroceryList.id == list_id
        ).execution_options(populate_existing=True)
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def run(self, household_id: uuid.UUID, list_id: uuid.UUID, key: str | None = None):
        statement = select(GroceryGenerationRun).where(
            GroceryGenerationRun.household_id == household_id,
            GroceryGenerationRun.grocery_list_id == list_id,
        )
        if key is not None:
            statement = statement.where(GroceryGenerationRun.idempotency_key == key)
        return self.session.scalar(statement.order_by(GroceryGenerationRun.created_at, GroceryGenerationRun.id))

    def items(self, run: GroceryGenerationRun):
        return list(self.session.scalars(
            select(GroceryListItem).where(
                GroceryListItem.grocery_list_id == run.grocery_list_id,
                GroceryListItem.generation_run_id == run.id,
            ).options(selectinload(GroceryListItem.recipe_sources))
            .execution_options(populate_existing=True)
        ))
