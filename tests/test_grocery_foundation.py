from decimal import Decimal
from io import StringIO
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, delete, event, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from alembic import command
from nourish_nest.config import get_settings
from nourish_nest.database import Base
from nourish_nest.models import (
    Food,
    FoodSourceType,
    GroceryItemSourceType,
    GroceryList,
    GroceryListItem,
    GroceryListStatus,
    Household,
)


@pytest.fixture(params=["metadata", "migration"])
def engine(request, tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'grocery.db'}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    db = create_engine(url)

    @event.listens_for(db, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    if request.param == "migration":
        command.upgrade(Config("alembic.ini"), "head")
    else:
        Base.metadata.create_all(db)
    yield db
    db.dispose()


def grocery(name="Weekly"):
    return GroceryList(
        household=Household(name=name), name=name,
        items=[GroceryListItem(display_name="Rice", required_quantity=Decimal("1.234567"),
                               required_unit="kg")],
    )


def test_defaults_decimal_roundtrip_and_household_scope(engine):
    with Session(engine) as session:
        first, second = grocery("First"), grocery("Second")
        session.add_all([first, second])
        session.commit()
        item = first.items[0]
        assert isinstance(first.id, UUID) and isinstance(item.id, UUID)
        assert first.status == GroceryListStatus.DRAFT
        assert first.version == item.version == 1
        assert item.required_quantity == Decimal("1.234567")
        assert isinstance(item.required_quantity, Decimal)
        assert item.purchased_quantity == Decimal(0)
        assert item.checked is False
        assert item.source_type == GroceryItemSourceType.MANUAL
        assert item.food_id is item.category is item.source_reference_id is None
        assert first.created_at and first.updated_at and item.created_at and item.updated_at
        scoped = select(GroceryListItem).join(GroceryList).where(
            GroceryList.household_id == second.household_id
        )
        assert session.scalars(scoped).all() == second.items
        assert session.scalar(scoped.where(GroceryListItem.id == item.id)) is None
        for status in GroceryListStatus:
            first.status = status
            session.commit()
            assert first.status == status
        for source in GroceryItemSourceType:
            item.source_type = source
            item.source_reference_id = uuid4()
            session.commit()
            assert item.source_type == source


@pytest.mark.parametrize("target", [GroceryList, Household])
def test_database_cascade_stays_within_household(engine, target):
    with Session(engine) as session:
        first, second = grocery("First"), grocery("Second")
        session.add_all([first, second])
        session.commit()
        removed_id, kept_id = first.items[0].id, second.items[0].id
        target_id = first.id if target is GroceryList else first.household_id
        session.execute(delete(target).where(target.id == target_id))
        session.commit()
        session.expunge_all()
        assert session.get(GroceryListItem, removed_id) is None
        assert session.get(GroceryListItem, kept_id) is not None


def test_food_deletion_is_restricted_and_orphan_removal_works(engine):
    with Session(engine) as session:
        listing = grocery()
        food = Food(name="Rice", normalized_name="rice", source_type=FoodSourceType.MANUAL,
                    serving_quantity=Decimal(1), serving_unit="kg")
        listing.items[0].food = food
        session.add(listing)
        session.commit()
        item_id = listing.items[0].id
        session.delete(food)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        listing.items.clear()
        session.commit()
        assert session.get(GroceryListItem, item_id) is None
        session.delete(food)
        session.commit()


@pytest.mark.parametrize(("model", "values"), [
    (GroceryList, {"status": "invalid"}),
    (GroceryList, {"household_id": uuid4()}),
    (GroceryList, {"version": 0}),
    (GroceryListItem, {"source_type": "invalid"}),
    (GroceryListItem, {"required_quantity": Decimal("-0.000001")}),
    (GroceryListItem, {"purchased_quantity": Decimal(-1)}),
    (GroceryListItem, {"grocery_list_id": uuid4()}),
    (GroceryListItem, {"food_id": uuid4()}),
    (GroceryListItem, {"version": 0}),
])
def test_database_rejects_invalid_values(engine, model, values):
    with Session(engine) as session:
        session.add(grocery())
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(update(model).values(**values))
            session.commit()
        session.rollback()


@pytest.mark.parametrize("model", [GroceryList, GroceryListItem])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_optimistic_concurrency(engine, model, operation):
    with Session(engine) as seed:
        listing = grocery()
        seed.add(listing)
        seed.commit()
        record_id = listing.id if model is GroceryList else listing.items[0].id
    with Session(engine) as first, Session(engine) as stale:
        current = first.get(model, record_id)
        old = stale.get(model, record_id)
        field = "name" if model is GroceryList else "display_name"
        previous_time = current.updated_at
        setattr(current, field, "Changed")
        first.commit()
        assert current.version == 2
        assert current.updated_at >= previous_time
        if operation == "delete":
            stale.delete(old)
        else:
            setattr(old, field, "Stale")
        with pytest.raises(StaleDataError):
            stale.commit()


def test_migration_roundtrip_and_metadata_match(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260908_0004")
    db = create_engine(url)
    with Session(db) as session:
        household = Household(name="Existing pantry household")
        session.add(household)
        session.commit()
        household_id = household.id
    command.upgrade(config, "head")
    with db.connect() as connection:
        # Existing food/pantry metadata drift is outside Phase 5A's migration scope.
        context = MigrationContext.configure(connection, opts={
            "include_object": lambda obj, name, kind, reflected, compare_to:
                name in {"grocery_lists", "grocery_list_items"} if kind == "table" else True,
            "compare_server_default": True,
        })
        diffs = compare_metadata(context, Base.metadata)
        assert diffs == []
        indexes = inspect(connection).get_indexes("grocery_list_items")
        assert {tuple(index["column_names"]) for index in indexes} == {
            ("grocery_list_id", "checked"), ("food_id",), ("generation_run_id",),
        }
        assert {tuple(index["column_names"]) for index in
                inspect(connection).get_indexes("grocery_lists")} == {
            ("household_id", "status"), ("status",),
        }
    command.downgrade(config, "20260908_0004")
    assert "grocery_lists" not in inspect(db).get_table_names()
    assert "grocery_list_items" not in inspect(db).get_table_names()
    assert "pantry_items" in inspect(db).get_table_names()
    command.upgrade(config, "head")
    with Session(db) as session:
        assert session.get(Household, household_id).name == "Existing pantry household"
    db.dispose()


def test_postgresql_migration_sql(monkeypatch):
    monkeypatch.setattr(get_settings(), "database_url", "postgresql://localhost/nourishnest")
    output = StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output),
                    "20260908_0004:20260908_0005", sql=True)
    ddl = output.getvalue()
    assert "NUMERIC(18, 6)" in ddl
    assert "UUID" in ddl
    assert "ON DELETE RESTRICT" in ddl
    assert "ON DELETE CASCADE" in ddl
    assert "DEFAULT false" in ddl
