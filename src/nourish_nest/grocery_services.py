import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from nourish_nest.grocery_repositories import GroceryRepository
from nourish_nest.grocery_schemas import (
    GroceryItemCreate,
    GroceryItemResponse,
    GroceryItemUpdate,
    GroceryListCreate,
    GroceryListResponse,
    GroceryListUpdate,
)
from nourish_nest.models import Food, GroceryList, GroceryListItem, utc_now
from nourish_nest.repositories import HouseholdRepository
from nourish_nest.services import NotFoundError


class StaleGroceryVersionError(RuntimeError):
    code = "stale_grocery_version"


class GroceryService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = GroceryRepository(session)
        self.households = HouseholdRepository(session)

    @contextmanager
    def _write(self) -> Iterator[None]:
        try:
            yield
            self.session.commit()
        except StaleDataError as exc:
            self.session.rollback()
            raise StaleGroceryVersionError("Grocery record version is stale") from exc
        except Exception:
            self.session.rollback()
            raise

    def _household(self, household_id: uuid.UUID) -> None:
        if self.households.get(household_id) is None:
            raise NotFoundError("Household not found")

    def _list(self, household_id: uuid.UUID, list_id: uuid.UUID) -> GroceryList:
        record = self.repo.get_list(household_id, list_id)
        if record is None:
            raise NotFoundError("Grocery list not found")
        return record

    def _item(self, household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID):
        record = self.repo.get_item(household_id, list_id, item_id)
        if record is None:
            raise NotFoundError("Grocery item not found")
        return record

    def _food(self, food_id: uuid.UUID | None) -> None:
        if food_id is not None and self.session.get(Food, food_id) is None:
            raise NotFoundError("Food not found")

    @staticmethod
    def _version(record: GroceryList | GroceryListItem, expected_version: int) -> None:
        if record.version != expected_version:
            raise StaleGroceryVersionError("Grocery record version is stale")

    def create_list(self, household_id: uuid.UUID, data: GroceryListCreate):
        with self._write():
            self._household(household_id)
            record = GroceryList(household_id=household_id, **data.model_dump())
            self.repo.add(record)
            self.session.flush()
            self.session.refresh(record)
            response = GroceryListResponse.model_validate(record)
        return response

    def list_lists(self, household_id: uuid.UUID):
        self._household(household_id)
        return [GroceryListResponse.model_validate(row) for row in self.repo.lists(household_id)]

    def get_list(self, household_id: uuid.UUID, list_id: uuid.UUID):
        return GroceryListResponse.model_validate(self._list(household_id, list_id))

    def update_list(self, household_id: uuid.UUID, list_id: uuid.UUID, data: GroceryListUpdate):
        with self._write():
            record = self._list(household_id, list_id)
            self._version(record, data.expected_version)
            for key, value in data.model_dump(exclude={"expected_version"}).items():
                setattr(record, key, value)
            # Even an identical PUT must issue a version-qualified UPDATE.
            record.updated_at = utc_now()
            self.session.flush()
            self.session.refresh(record)
            response = GroceryListResponse.model_validate(record)
        return response

    def delete_list(self, household_id: uuid.UUID, list_id: uuid.UUID, expected_version: int):
        with self._write():
            record = self._list(household_id, list_id)
            self._version(record, expected_version)
            self.repo.delete(record)

    def create_item(self, household_id: uuid.UUID, list_id: uuid.UUID, data: GroceryItemCreate):
        with self._write():
            self._list(household_id, list_id)
            self._food(data.food_id)
            record = GroceryListItem(grocery_list_id=list_id, **data.model_dump())
            self.repo.add(record)
            self.session.flush()
            self.session.refresh(record)
            response = GroceryItemResponse.model_validate(record)
        return response

    def list_items(self, household_id: uuid.UUID, list_id: uuid.UUID):
        self._list(household_id, list_id)
        return [GroceryItemResponse.model_validate(row)
                for row in self.repo.items(household_id, list_id)]

    def get_item(self, household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID):
        return GroceryItemResponse.model_validate(self._item(household_id, list_id, item_id))

    def update_item(
        self, household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID, data: GroceryItemUpdate
    ):
        with self._write():
            record = self._item(household_id, list_id, item_id)
            self._version(record, data.expected_version)
            self._food(data.food_id)
            for key, value in data.model_dump(exclude={"expected_version"}).items():
                setattr(record, key, value)
            record.updated_at = utc_now()
            self.session.flush()
            self.session.refresh(record)
            response = GroceryItemResponse.model_validate(record)
        return response

    def delete_item(
        self, household_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID, expected_version: int
    ):
        with self._write():
            record = self._item(household_id, list_id, item_id)
            self._version(record, expected_version)
            self.repo.delete(record)
