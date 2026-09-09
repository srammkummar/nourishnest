from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import StringIO
from threading import Barrier, local
from uuid import UUID

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select
from test_database_phase2 import food_payload, recipe_payload
from test_database_phase2 import (
    phase2_client as phase2_client,  # noqa: PLC0414 -- pytest fixture export
)
from test_grocery_generation_foundation import seed

from alembic import command
from nourish_nest.config import get_settings
from nourish_nest.food_schemas import RecipeCreate, RecipeUpdate
from nourish_nest.models import Recipe, RecipeCreationRecord, RecipeIngredient
from nourish_nest.repositories import RecipeRepository
from nourish_nest.services import RecipeMutationError, RecipeService


@pytest.fixture
def setup_recipe(phase2_client):
    client, factory = phase2_client
    home = client.post("/v1/households", json={"name": "Home"}).json()["id"]
    food = client.post("/v1/foods", json=food_payload()).json()["id"]
    return client, factory, UUID(home), recipe_payload([food])


def create(client, home, payload, key="key"):
    return client.post(
        f"/v1/households/{home}/recipes",
        json=payload,
        headers={"Idempotency-Key": key, "x-request-id": "recipe-trace"},
    )


def test_versioned_mutations_and_required_contract(setup_recipe):
    client, _, home, payload = setup_recipe
    assert client.post(f"/v1/households/{home}/recipes", json=payload).status_code == 422
    for key in [" ", "x" * 129]:
        assert create(client, home, payload, key).status_code == 422
    result = create(client, home, payload)
    assert result.status_code == 201 and result.json()["version"] == 1
    url = f"/v1/households/{home}/recipes/{result.json()['id']}"
    assert client.put(url, json=payload).status_code == 422
    assert client.delete(url).status_code == 422
    update = {**payload, "expected_version": 1}
    update["instructions"] = [{"step_number": 1, "instruction": "Changed instruction only"}]
    assert client.put(url, json=update).json()["version"] == 2
    for response in [
        client.put(url, json=update, headers={"x-request-id": "trace"}),
        client.delete(url, params={"expected_version": 1}, headers={"x-request-id": "trace"}),
    ]:
        assert response.status_code == 409
        assert response.json()["code"] == "stale_recipe_version"
        assert response.json()["request_id"] == response.headers["x-request-id"] == "trace"
    assert client.delete(url, params={"expected_version": 2}).status_code == 204
    assert create(client, home, payload).json()["code"] == "idempotency_result_deleted"


def test_creation_replay_and_conflict(setup_recipe):
    client, factory, home, payload = setup_recipe
    first = create(client, home, payload).json()
    equivalent = {**payload, "servings": "2.00"}
    replay = create(client, home, equivalent)
    assert replay.status_code == 201
    assert replay.json()["id"] == first["id"] and replay.json()["version"] == 1
    assert Decimal(replay.json()["servings"]) == Decimal(first["servings"])
    assert [i["id"] for i in replay.json()["ingredients"]] == [
        i["id"] for i in first["ingredients"]
    ]
    conflict = create(client, home, {**payload, "name": "Different"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    assert conflict.json()["request_id"] == "recipe-trace"
    with factory() as session:
        assert len(session.scalars(select(Recipe)).all()) == 1
        assert len(session.scalars(select(RecipeCreationRecord)).all()) == 1
    other = client.post("/v1/households", json={"name": "Other"}).json()["id"]
    assert create(client, other, payload).json()["id"] != first["id"]


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_two_session_stale_write(setup_recipe, operation):
    client, factory, home, payload = setup_recipe
    rid = UUID(create(client, home, payload).json()["id"])
    with factory() as first, factory() as second:
        service = RecipeService(second)
        stale = service.get(home, rid)  # Keep identity-map snapshot alive.
        RecipeService(first).update(home, rid, RecipeUpdate(**payload, expected_version=1))
        assert stale.version == 1
        with pytest.raises(RecipeMutationError) as error:
            if operation == "update":
                service.update(home, rid, RecipeUpdate(**payload, expected_version=1))
            else:
                service.delete(home, rid, 1)
        assert error.value.code == "stale_recipe_version"
        second.expire_all()
        assert service.get(home, rid).version == 2


@pytest.mark.parametrize("different", [False, True])
def test_concurrent_database_duplicate_protection(setup_recipe, monkeypatch, different):
    _, factory, home, payload = setup_recipe
    barrier, state = Barrier(2), local()
    original = RecipeRepository.creation_record

    def simultaneous_lookup(self, household_id, key):
        record = original(self, household_id, key)
        if not getattr(state, "queried", False):
            state.queried = True
            assert record is None
            barrier.wait(timeout=10)
        return record

    monkeypatch.setattr(RecipeRepository, "creation_record", simultaneous_lookup)

    def worker(index):
        data = {**payload, "name": f"Recipe {index}" if different else "Same"}
        with factory() as session:
            try:
                return RecipeService(session).create(home, RecipeCreate(**data), "race").id
            except RecipeMutationError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, [1, 2]))
    if different:
        assert results.count("idempotency_conflict") == 1
    else:
        assert results[0] == results[1]
    with factory() as session:
        assert len(session.scalars(select(Recipe)).all()) == 1
        assert len(session.scalars(select(RecipeCreationRecord)).all()) == 1
        assert len(session.scalars(select(RecipeIngredient)).all()) == len(payload["ingredients"])


def test_create_atomic_rollback(setup_recipe, monkeypatch):
    _, factory, home, payload = setup_recipe
    with factory() as session:

        def fail_commit():
            assert session.scalar(select(RecipeCreationRecord)) is not None
            raise RuntimeError("forced commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="forced"):
            RecipeService(session).create(home, RecipeCreate(**payload), "rollback")
    with factory() as session:
        for model in (Recipe, RecipeCreationRecord, RecipeIngredient):
            assert session.scalar(select(model)) is None
        assert RecipeService(session).create(home, RecipeCreate(**payload), "rollback").version == 1


def test_lineage_restriction_and_system_immutability(phase2_client):
    client, factory = phase2_client
    with factory() as session:
        home, _, _, _, recipe, ingredient, source = seed(session)
        home_id, rid, source_id = home.id, recipe.id, source.id
        payload = recipe_payload([str(ingredient.food_id)])
        shared = Recipe(name="Shared", servings=1)
        session.add(shared)
        session.commit()
        shared_id = shared.id
    url = f"/v1/households/{home_id}/recipes/{rid}"
    result = client.delete(url, params={"expected_version": 1}, headers={"x-request-id": "lineage"})
    assert result.status_code == 409 and result.json()["code"] == "recipe_in_use"
    assert result.json()["request_id"] == "lineage"
    for response in [
        client.delete(f"/v1/households/{home_id}/recipes/{shared_id}?expected_version=1"),
        client.put(
            f"/v1/households/{home_id}/recipes/{shared_id}", json={**payload, "expected_version": 1}
        ),
    ]:
        assert response.status_code == 403
    with factory() as session:
        assert session.get(Recipe, rid).version == 1
        assert session.get(type(source), source_id) is not None


def test_migration_roundtrip_and_postgresql(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text

    url = f"sqlite:///{tmp_path / 'recipe_migration.db'}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260909_0008")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO recipes (id,name,servings,preparation_minutes,cooking_minutes,created_at,updated_at) VALUES (:id,'Legacy',2,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"id": UUID(int=1).hex},
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version FROM recipes")) == 1
    assert inspect(engine).get_unique_constraints("recipe_creation_records")[0]["column_names"] == [
        "household_id",
        "idempotency_key",
    ]
    command.downgrade(config, "-1")
    assert "version" not in {c["name"] for c in inspect(engine).get_columns("recipes")}
    command.upgrade(config, "head")
    engine.dispose()
    monkeypatch.setattr(get_settings(), "database_url", "postgresql://test:test@localhost/test")
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "20260909_0008:20260909_0009", sql=True)
    command.downgrade(config, "20260909_0009:20260909_0008", sql=True)
    ddl = output.getvalue()
    assert "ON DELETE SET NULL" in ddl and "ON DELETE CASCADE" in ddl
    assert "UNIQUE (household_id, idempotency_key)" in ddl
    assert "ADD COLUMN version INTEGER" in ddl and "DROP COLUMN version" in ddl
