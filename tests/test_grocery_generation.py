from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from test_grocery_crud import grocery_client  # noqa: F401
from test_grocery_requirements import preview_data  # noqa: F401
from test_grocery_shortage import add_lot, shortage_data  # noqa: F401

from nourish_nest.grocery_generation_repositories import GroceryGenerationRepository
from nourish_nest.grocery_generation_schemas import GroceryGenerationRequest
from nourish_nest.grocery_generation_services import (
    GroceryGenerationError,
    GroceryGenerationService,
    canonical_request_hash,
)
from nourish_nest.grocery_shortage_services import GroceryShortageService
from nourish_nest.models import (
    GroceryGenerationRun,
    GroceryItemRecipeSource,
    GroceryList,
    GroceryListItem,
    GroceryListStatus,
    PantryItem,
    PantryTransaction,
)


@pytest.fixture
def generation_data(request):
    data = request.getfixturevalue("shortage_data")
    client, _, home, _, recipes = data
    listing = client.post(f"/v1/households/{home}/grocery-lists", json={"name": "Weekly"}).json()
    list_id = UUID(listing["id"])
    payload = {"recipes": [{"recipe_id": str(recipes["first"]), "desired_servings": 4}],
               "expected_list_version": 1, "idempotency_key": "generate-1"}
    return data, list_id, payload


def generate(data, list_id=None, payload=None, household=None):
    preview, target, original = data
    client, _, home, *_ = preview
    return client.post(
        f"/v1/households/{household or home}/grocery-lists/{list_id or target}/generations",
        json=payload or original, headers={"x-request-id": "generation-1"},
    )


def counts(session):
    return [len(session.scalars(select(model)).all())
            for model in [GroceryGenerationRun, GroceryListItem, GroceryItemRecipeSource]]


def test_atomic_generation_lineage_and_manual_preservation(generation_data, monkeypatch):
    data, list_id, payload = generation_data
    client, sessions, home, _, recipes = data
    path = f"/v1/households/{home}/grocery-lists/{list_id}"
    manual = client.post(f"{path}/items", json={"display_name": "Manual", "required_quantity": "1",
                                              "required_unit": "item"}).json()
    add_lot(data, "0.125", "kg")
    with sessions() as session:
        pantry_before = session.execute(select(PantryItem.__table__)).all()
    payload["recipes"].append({"recipe_id": str(recipes["second"]), "desired_servings": 4})
    calls = []
    original = GroceryShortageService.preview

    def preview(service, household, selections):
        calls.append(household)
        return original(service, household, selections)

    monkeypatch.setattr(GroceryShortageService, "preview", preview)
    response = generate(generation_data)
    assert response.status_code == 200, response.text
    result = response.json()
    assert calls == [home]
    assert result["grocery_list_version"] == 2
    assert result["replayed"] is False and result["warnings_available"] is True
    assert result["calculation_version"] == "grocery-generation-v1"
    item, = result["created_items"]
    assert Decimal(item["required_quantity"]) == Decimal(275)
    assert Decimal(item["purchased_quantity"]) == Decimal(0) and item["checked"] is False
    assert item["source_type"] == "recipe" and item["generation_run_id"] == result["generation_run_id"]
    assert {source["recipe_id"] for source in item["recipe_sources"]} == {
        str(recipes["first"]), str(recipes["second"]),
    }
    assert sum((Decimal(source["required_quantity"]) for source in item["recipe_sources"]), Decimal(0)) == Decimal(400)
    assert all(source["recipe_ingredient_id"] for source in item["recipe_sources"])
    assert client.get(f"{path}/items/{manual['id']}").json() == manual
    with sessions() as session:
        assert counts(session) == [1, 2, 2]
        assert session.execute(select(PantryItem.__table__)).all() == pantry_before
        assert session.scalar(select(PantryTransaction)) is None


def test_empty_generation_is_success_and_blocks_another(generation_data):
    data, _, payload = generation_data
    add_lot(data, "1000")
    response = generate(generation_data)
    assert response.status_code == 200
    assert response.json()["created_items"] == []
    assert response.json()["grocery_list_version"] == 2
    assert generate(generation_data).json()["replayed"] is True
    response = generate(generation_data, payload={**payload, "idempotency_key": "another", "expected_list_version": 2})
    assert response.status_code == 409 and response.json()["code"] == "grocery_generation_exists"


def test_exact_decimal_shortage(generation_data):
    data, _, payload = generation_data
    payload["recipes"][0]["desired_servings"] = "0.006"
    add_lot(data, "0.0001", "kg")
    response = generate(generation_data)
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["created_items"][0]["required_quantity"]) == Decimal("0.2")


def test_canonical_replay_does_not_recalculate_or_write(generation_data, monkeypatch):
    data, list_id, payload = generation_data
    _, sessions, _, _, recipes = data
    payload["recipes"].append({"recipe_id": str(recipes["second"]), "desired_servings": 4})
    add_lot(data, "1", "pinch")
    original = generate(generation_data).json()
    assert original["warnings"][0]["code"] == "unsupported_pantry_conversion"
    with sessions() as session:
        listing = session.get(GroceryList, list_id)
        listing.status = GroceryListStatus.COMPLETED
        session.commit()

    def no_preview(*args):
        pytest.fail("Replay must not recalculate shortages")

    monkeypatch.setattr(GroceryShortageService, "preview", no_preview)
    payload["recipes"].reverse()
    payload["recipes"][0]["desired_servings"] = "4.000"
    with sessions() as session:
        db = session.get_bind()
        writes = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
                writes.append(statement)

        event.listen(db, "before_cursor_execute", capture)
        try:
            replay = GroceryGenerationService(session).generate(data[2], list_id,
                                                                GroceryGenerationRequest(**payload))
        finally:
            event.remove(db, "before_cursor_execute", capture)
        assert writes == []
        assert replay.replayed and not replay.warnings_available and replay.warnings == []
        assert str(replay.generation_run_id) == original["generation_run_id"]
        assert replay.created_items[0].id == UUID(original["created_items"][0]["id"])
        assert replay.calculation_as_of.isoformat().replace("+00:00", "Z") == original["calculation_as_of"]
        assert counts(session) == [1, 1, 2]


@pytest.mark.parametrize("change", ["recipes", "expected_list_version"])
def test_conflicting_replay(generation_data, change):
    _, _, payload = generation_data
    assert generate(generation_data).status_code == 200
    if change == "recipes":
        payload["recipes"][0]["desired_servings"] = 8
    else:
        payload["expected_list_version"] = 2
    response = generate(generation_data)
    assert response.status_code == 409 and response.json()["code"] == "idempotency_conflict"


@pytest.mark.parametrize(("status", "version", "code"), [
    (GroceryListStatus.DRAFT, 2, "stale_grocery_version"),
    (GroceryListStatus.COMPLETED, 1, "invalid_grocery_list_status"),
    (GroceryListStatus.ARCHIVED, 1, "invalid_grocery_list_status"),
])
def test_status_and_version_errors(generation_data, status, version, code):
    data, list_id, payload = generation_data
    with data[1]() as session:
        listing = session.get(GroceryList, list_id)
        listing.status = status
        session.commit()
    response = generate(generation_data, payload={**payload, "expected_list_version": version})
    assert response.status_code == 409 and response.json()["code"] == code
    assert response.json()["request_id"] == response.headers["x-request-id"] == "generation-1"
    assert set(response.json()) == {"code", "message", "request_id"}


def test_isolation_and_invalid_contract(generation_data):
    data, list_id, payload = generation_data
    client, sessions, home, _, recipes = data
    other = client.post("/v1/households", json={"name": "Other"}).json()["id"]
    for kwargs in [{"household": other}, {"household": uuid4()}, {"list_id": uuid4()}]:
        assert generate(generation_data, **kwargs).status_code == 404
    payload["recipes"][0]["recipe_id"] = str(recipes["foreign"])
    assert generate(generation_data).status_code == 404
    payload["recipes"][0]["recipe_id"] = str(recipes["first"])
    for invalid in [None, "", "   ", "x" * 201]:
        assert generate(generation_data, payload={**payload, "idempotency_key": invalid}).status_code == 422
    assert generate(generation_data, payload={key: value for key, value in payload.items()
                                             if key != "expected_list_version"}).status_code == 422
    with sessions() as session:
        assert counts(session) == [0, 0, 0]
        assert session.get(GroceryList, list_id).version == 1
        assert session.get(GroceryList, list_id).household_id == home


@pytest.mark.parametrize("stage", ["grocery_lists", "grocery_generation_runs", "grocery_list_items",
                                    "grocery_item_recipe_sources", "commit"])
def test_failure_rolls_back_everything_and_never_mutates_pantry(generation_data, monkeypatch, stage):
    data, list_id, payload = generation_data
    add_lot(data, "50")
    _, sessions, home, *_ = data
    with sessions() as session:
        before = session.execute(select(PantryItem.__table__)).all()
        db = session.get_bind()

        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith((f"UPDATE {stage} ", f"INSERT INTO {stage} ")):
                raise SQLAlchemyError("Injected failure")

        def fail_commit():
            raise SQLAlchemyError("Injected commit failure")

        if stage == "commit":
            monkeypatch.setattr(session, "commit", fail_commit)
        event.listen(db, "after_cursor_execute", fail)
        try:
            with pytest.raises(SQLAlchemyError):
                GroceryGenerationService(session).generate(home, list_id, GroceryGenerationRequest(**payload))
        finally:
            event.remove(db, "after_cursor_execute", fail)
        assert not session.in_transaction()
        assert counts(session) == [0, 0, 0]
        assert session.get(GroceryList, list_id).version == 1
        assert session.execute(select(PantryItem.__table__)).all() == before
        assert session.scalar(select(PantryTransaction)) is None


@pytest.mark.parametrize("conflict", [False, True])
def test_database_unique_constraint_recovery(generation_data, monkeypatch, conflict):
    data, list_id, payload = generation_data
    _, sessions, home, *_ = data
    request = GroceryGenerationRequest(**payload)
    with sessions() as session:
        winner = GroceryGenerationRun(household_id=home, grocery_list_id=list_id,
                                       idempotency_key=request.idempotency_key,
                                       request_hash="different" if conflict else canonical_request_hash(request),
                                       calculation_version="grocery-generation-v1")
        session.add(winner)
        session.commit()
        winner_id = winner.id
        service = GroceryGenerationService(session)
        original = service.repo.run
        queries = 0

        def missed_prequery(*args):
            nonlocal queries
            queries += 1
            return None if queries <= 2 else original(*args)

        monkeypatch.setattr(service.repo, "run", missed_prequery)
        if conflict:
            with pytest.raises(GroceryGenerationError, match="different request"):
                service.generate(home, list_id, request)
        else:
            response = service.generate(home, list_id, request)
            assert response.replayed and response.generation_run_id == winner_id
        assert queries >= 3
        assert counts(session) == [1, 0, 0]
        assert session.get(GroceryList, list_id).version == 1


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_generation(generation_data, monkeypatch, same_key):
    data, list_id, payload = generation_data
    _, sessions, home, *_ = data
    barrier = Barrier(2)
    original = GroceryGenerationRepository.get_list

    def synchronized(repo, household, target, lock=False):
        result = original(repo, household, target, lock)
        if lock:
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(GroceryGenerationRepository, "get_list", synchronized)

    def attempt(index):
        with sessions() as session:
            request = GroceryGenerationRequest(**{**payload,
                "idempotency_key": payload["idempotency_key"] if same_key else f"key-{index}"})
            try:
                return GroceryGenerationService(session).generate(home, list_id, request)
            except GroceryGenerationError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [0, 1]))
    if same_key:
        assert sorted(result.replayed for result in results) == [False, True]
        assert results[0].generation_run_id == results[1].generation_run_id
    else:
        assert sum(result == "grocery_generation_exists" for result in results) == 1
    with sessions() as session:
        assert counts(session) == [1, 1, 1]
        assert session.get(GroceryList, list_id).version == 2


def test_unrepresentable_quantity_is_not_silently_rounded(generation_data):
    data, list_id, payload = generation_data
    payload["recipes"] = [{"recipe_id": str(data[-1]["volume"]), "desired_servings": 2}]
    response = generate(generation_data)
    assert response.status_code == 422  # One cup = 236.5882365 ml; seven decimal places.
    assert response.json()["code"] == "invalid_request"
    with data[1]() as session:
        assert counts(session) == [0, 0, 0]
        assert session.get(GroceryList, list_id).version == 1
