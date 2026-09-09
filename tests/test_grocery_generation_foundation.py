from decimal import Decimal
from io import StringIO
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine, delete, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from nourish_nest.config import get_settings
from nourish_nest.database import Base
from nourish_nest.models import (
    Food,
    FoodSourceType,
    GroceryGenerationRun,
    GroceryItemRecipeSource,
    GroceryList,
    GroceryListItem,
    Household,
    Recipe,
    RecipeIngredient,
    utc_now,
)


@pytest.fixture(params=["metadata", "migration"])
def generation_db(request, tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'generation.db'}"
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


def seed(session):
    home = Household(name="Home")
    listing = GroceryList(household=home, name="Weekly")
    run = GroceryGenerationRun(household=home, grocery_list=listing, idempotency_key="request-1",
                               request_hash="a" * 64, calculation_version="grocery-shortage-v1")
    item = GroceryListItem(grocery_list=listing, generation_run=run, display_name="Rice",
                           required_quantity=Decimal("1.234567"), required_unit="g")
    food = Food(name="Rice", normalized_name="rice", source_type=FoodSourceType.MANUAL,
                serving_quantity=Decimal(1), serving_unit="g")
    recipe = Recipe(household=home, name="Rice dish", servings=Decimal(2))
    ingredient = RecipeIngredient(recipe=recipe, food=food, quantity=Decimal(1), unit="g", display_order=0)
    source = GroceryItemRecipeSource(grocery_list_item=item, recipe=recipe,
                                     recipe_ingredient=ingredient, required_quantity=Decimal("1.234567"),
                                     canonical_unit="g")
    session.add(source)
    session.commit()
    return home, listing, run, item, recipe, ingredient, source


def test_relationships_decimal_and_manual_compatibility(generation_db):
    with Session(generation_db) as session:
        home, listing, run, item, recipe, ingredient, source = seed(session)
        session.expire_all()
        assert run.household == home and run.grocery_list == listing
        assert listing.generation_runs == [run] and run.items == [item]
        assert item.recipe_sources == [source] and source.recipe == recipe
        assert source.recipe_ingredient == ingredient
        assert isinstance(source.id, UUID) and isinstance(run.id, UUID)
        assert isinstance(source.required_quantity, Decimal)
        assert source.required_quantity == Decimal("1.234567")
        assert run.created_at and source.created_at
        manual = GroceryListItem(grocery_list=listing, display_name="Manual", required_quantity=Decimal(1),
                                 required_unit="item")
        session.add(manual)
        session.commit()
        assert manual.generation_run_id is None and manual.generation_run is None
        assert manual.recipe_sources == []


def test_idempotency_key_scope(generation_db):
    with Session(generation_db) as session:
        home, listing, run, *_ = seed(session)
        duplicate = GroceryGenerationRun(household_id=home.id, grocery_list_id=listing.id,
                                          idempotency_key=run.idempotency_key, request_hash="b" * 64,
                                          calculation_version="v1")
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        for owner in [home, Household(name="Other")]:
            other_list = GroceryList(household=owner, name="Other list")
            session.add(GroceryGenerationRun(household=owner, grocery_list=other_list,
                                             idempotency_key="request-1", request_hash="a" * 64,
                                             calculation_version="v1"))
        session.commit()
        assert len(session.scalars(select(GroceryGenerationRun)).all()) == 3


@pytest.mark.parametrize("target", [Household, GroceryList, GroceryListItem])
@pytest.mark.parametrize("raw", [False, True])
def test_cascades(generation_db, target, raw):
    with Session(generation_db) as session:
        home, listing, run, item, _, _, source = seed(session)
        target_record = {Household: home, GroceryList: listing, GroceryListItem: item}[target]
        run_id, source_id, item_id = run.id, source.id, item.id
        if raw:
            session.execute(delete(target).where(target.id == target_record.id))
        else:
            session.delete(target_record)
        session.commit()
        session.expunge_all()
        assert session.get(GroceryItemRecipeSource, source_id) is None
        assert session.get(GroceryListItem, item_id) is None
        assert (session.get(GroceryGenerationRun, run_id) is None) == (target is not GroceryListItem)


def test_recipe_restriction_and_optional_reference_deletion(generation_db):
    with Session(generation_db) as session:
        _, _, run, item, recipe, ingredient, source = seed(session)
        session.delete(recipe)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert session.get(Recipe, recipe.id) is recipe
        session.delete(ingredient)
        session.delete(run)
        session.commit()
        session.expire_all()
        assert item.generation_run_id is None
        assert source.recipe_ingredient_id is None
        assert source.recipe_id == recipe.id
        assert source.required_quantity == Decimal("1.234567")
        session.delete(source)
        session.delete(recipe)
        session.commit()


def test_foreign_keys_and_quantity_check(generation_db):
    with Session(generation_db) as session:
        _, _, _, item, recipe, _, _ = seed(session)
        for fields in [
            {"recipe_id": uuid4()},
            {"recipe_ingredient_id": uuid4()},
            {"grocery_list_item_id": uuid4()},
            {"required_quantity": Decimal(-1)},
        ]:
            values = {"grocery_list_item_id": item.id, "recipe_id": recipe.id,
                      "required_quantity": Decimal(1), "canonical_unit": "g", **fields}
            session.add(GroceryItemRecipeSource(**values))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()


def test_migration_roundtrip_preserves_manual_items(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260908_0005")
    db = create_engine(url)
    old = MetaData()
    old.reflect(db)
    home_id, list_id, item_id = (uuid4().hex for _ in range(3))
    with db.begin() as connection:
        connection.execute(old.tables["households"].insert().values(
            id=home_id, name="Existing", timezone="UTC", currency="USD", created_at=utc_now(), updated_at=utc_now()))
        connection.execute(old.tables["grocery_lists"].insert().values(
            id=list_id, household_id=home_id, name="Manual list", created_at=utc_now(), updated_at=utc_now()))
        connection.execute(old.tables["grocery_list_items"].insert().values(
            id=item_id, grocery_list_id=list_id, display_name="Manual", required_quantity=Decimal("1.234567"),
            required_unit="g", created_at=utc_now(), updated_at=utc_now()))
    command.upgrade(config, "head")
    with db.connect() as connection:
        names = {"grocery_lists", "grocery_list_items", "grocery_generation_runs", "grocery_item_recipe_sources"}
        context = MigrationContext.configure(connection, opts={
            "include_object": lambda obj, name, kind, reflected, compare_to:
                name in names if kind == "table" else True,
            "compare_server_default": True,
        })
        assert compare_metadata(context, Base.metadata) == []
        for table, columns in {
            "grocery_generation_runs": {"household_id", "grocery_list_id", "created_at"},
            "grocery_item_recipe_sources": {"grocery_list_item_id", "recipe_id", "recipe_ingredient_id"},
        }.items():
            assert {tuple(index["column_names"]) for index in inspect(connection).get_indexes(table)} == {
                (column,) for column in columns
            }
    with Session(db) as session:
        item = session.get(GroceryListItem, UUID(item_id))
        assert item.generation_run_id is None and item.required_quantity == Decimal("1.234567")
    command.downgrade(config, "-1")
    assert "grocery_generation_runs" not in inspect(db).get_table_names()
    assert "grocery_item_recipe_sources" not in inspect(db).get_table_names()
    assert "generation_run_id" not in {column["name"] for column in inspect(db).get_columns("grocery_list_items")}
    with db.connect() as connection:
        assert connection.scalar(select(old.tables["grocery_list_items"].c.id)) == item_id
    command.upgrade(config, "head")
    with Session(db) as session:
        assert session.get(GroceryListItem, UUID(item_id)).required_quantity == Decimal("1.234567")
    db.dispose()


def test_offline_postgresql_upgrade_and_downgrade(monkeypatch):
    monkeypatch.setattr(get_settings(), "database_url", "postgresql://localhost/nourishnest")
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "20260908_0005:20260908_0006", sql=True)
    command.downgrade(config, "20260908_0006:20260908_0005", sql=True)
    ddl = output.getvalue()
    for fragment in ["UUID", "NUMERIC(18, 6)", "ON DELETE CASCADE", "ON DELETE SET NULL",
                     "ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED",
                     "UNIQUE (household_id, grocery_list_id, idempotency_key)",
                     "DROP COLUMN generation_run_id"]:
        assert fragment in ddl
