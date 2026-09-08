from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from test_grocery_crud import grocery_client  # noqa: F401
from test_grocery_requirements import preview_data  # noqa: F401

from nourish_nest.grocery_requirement_schemas import GroceryRequirementsRequest
from nourish_nest.grocery_requirement_services import GroceryRequirementsService
from nourish_nest.grocery_shortage_services import GroceryShortageService
from nourish_nest.models import (
    Base,
    GroceryList,
    Household,
    PantryItem,
    PantryItemStatus,
    PantryLocation,
    PantryLocationType,
)

AS_OF = datetime(2026, 9, 8, 12, tzinfo=UTC)


@pytest.fixture
def shortage_data(request, monkeypatch):
    monkeypatch.setattr("nourish_nest.grocery_shortage_services.utc_now", lambda: AS_OF)
    return request.getfixturevalue("preview_data")


def add_lot(data, quantity, unit="g", status=PantryItemStatus.ACTIVE, expiration=None, household=None):
    _, sessions, home, food, _ = data
    with sessions() as session:
        location = PantryLocation(household_id=household or home, name=str(uuid4()),
                                  location_type=PantryLocationType.PANTRY)
        lot = PantryItem(household_id=household or home, location=location, food_id=food,
                         quantity=Decimal(quantity), unit=unit, status=status, expiration_date=expiration)
        session.add(lot)
        session.commit()
        return lot.id


def shortage(data, recipe=None, servings=4, household=None):
    client, _, home, _, recipes = data
    return client.post(
        f"/v1/households/{household or home}/grocery-requirements/shortage-preview",
        json={"recipes": [{"recipe_id": str(recipe or recipes["first"]), "desired_servings": servings}]},
        headers={"x-request-id": "shortage-1"},
    )


@pytest.mark.parametrize(("lots", "available", "remaining"), [
    ([], "0", "200"),
    ([("200", "g")], "200", "0"),
    ([("50", "g")], "50", "150"),
    ([("0.1", "kg"), ("25", "g")], "125", "75"),
    ([("300", "g")], "300", "0"),
])
def test_coverage_and_lot_contributions(shortage_data, lots, available, remaining):
    ids = [add_lot(shortage_data, quantity, unit) for quantity, unit in lots]
    response = shortage(shortage_data)
    assert response.status_code == 200, response.text
    assert response.headers["x-request-id"] == "shortage-1"
    body = response.json()
    assert body["calculation_as_of"] == "2026-09-08T12:00:00Z"
    assert body["calculation_version"] == "grocery-shortage-v1"
    row, = body["requirements"]
    assert Decimal(row["required_quantity"]) == Decimal(200)
    assert Decimal(row["available_quantity"]) == Decimal(available)
    assert Decimal(row["shortage_quantity"]) == Decimal(remaining)
    assert row["purchase_required"] == (Decimal(remaining) > 0)
    assert {lot["pantry_item_id"] for lot in row["pantry_lots"]} == {str(value) for value in ids}
    assert sum((Decimal(lot["available_quantity"]) for lot in row["pantry_lots"]), Decimal(0)) == Decimal(available)
    assert row["sources"] and body["warnings"] == []


@pytest.mark.parametrize(("status", "quantity", "expiration", "available", "expired_warning"), [
    (PantryItemStatus.ACTIVE, "100", AS_OF.date() - timedelta(days=1), "0", True),
    (PantryItemStatus.EXPIRED, "100", None, "0", True),
    (PantryItemStatus.DEPLETED, "100", None, "0", False),
    (PantryItemStatus.DISCARDED, "100", None, "0", False),
    (PantryItemStatus.ACTIVE, "0", None, "0", False),
    (PantryItemStatus.ACTIVE, "100", AS_OF.date(), "100", False),
    (PantryItemStatus.ACTIVE, "100", AS_OF.date() + timedelta(days=1), "100", False),
])
def test_lot_eligibility(shortage_data, status, quantity, expiration, available, expired_warning):
    lot_id = add_lot(shortage_data, quantity, status=status, expiration=expiration)
    body = shortage(shortage_data).json()
    assert Decimal(body["requirements"][0]["available_quantity"]) == Decimal(available)
    warnings = body["warnings"]
    assert bool(warnings) == expired_warning
    if expired_warning:
        assert warnings[0]["code"] == "excluded_expired_lot"
        assert warnings[0]["pantry_item_id"] == str(lot_id)


def test_household_isolation_and_errors(shortage_data):
    _, sessions, _, _, recipes = shortage_data
    with sessions() as session:
        other = Household(name="Other pantry")
        session.add(other)
        session.commit()
        other_id = other.id
    add_lot(shortage_data, "1000", household=other_id)
    add_lot(shortage_data, "1000", household=other_id, expiration=date(2000, 1, 1))
    body = shortage(shortage_data).json()
    assert Decimal(body["requirements"][0]["available_quantity"]) == Decimal(0)
    assert body["warnings"] == []
    for kwargs in [{"household": uuid4()}, {"recipe": recipes["foreign"]}, {"recipe": uuid4()}]:
        response = shortage(shortage_data, **kwargs)
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
        assert response.json()["request_id"] == response.headers["x-request-id"] == "shortage-1"
        assert set(response.json()) == {"code", "message", "request_id"}
    assert shortage(shortage_data, recipe=recipes["system"]).status_code == 200
    assert shortage(shortage_data, servings=0).status_code == 422


def test_exact_decimal_subtraction(shortage_data):
    add_lot(shortage_data, "0.0001", "kg")
    row = shortage(shortage_data, servings="0.006").json()["requirements"][0]
    assert Decimal(row["required_quantity"]) == Decimal("0.3")
    assert Decimal(row["available_quantity"]) == Decimal("0.1")
    assert Decimal(row["shortage_quantity"]) == Decimal("0.2")


def test_conversion_warnings_and_incomplete_requirements(shortage_data):
    liquid = add_lot(shortage_data, "1", "l")
    unsupported = add_lot(shortage_data, "1", "pinch")
    body = shortage(shortage_data).json()
    assert Decimal(body["requirements"][0]["available_quantity"]) == Decimal(0)
    warnings = {warning["code"]: warning for warning in body["warnings"]}
    assert warnings["incompatible_pantry_units"]["pantry_item_id"] == str(liquid)
    assert warnings["incompatible_pantry_units"]["requirement_canonical_unit"] == "g"
    assert warnings["incompatible_pantry_units"]["pantry_canonical_unit"] == "ml"
    assert warnings["unsupported_pantry_conversion"]["pantry_item_id"] == str(unsupported)
    body = shortage(shortage_data, recipe=shortage_data[-1]["unknown"]).json()
    assert body["requirements"] == []
    assert body["warnings"][0]["code"] == "unsupported_conversion"
    assert body["warnings"][0]["recipe_id"] == str(shortage_data[-1]["unknown"])


def test_ordering_and_separate_dimensions(shortage_data):
    client, _, home, _, recipes = shortage_data
    ids = [add_lot(shortage_data, "50"), add_lot(shortage_data, "0.1", "kg")]
    add_lot(shortage_data, "1", "l")
    selections = [{"recipe_id": str(recipes[name]), "desired_servings": 4}
                  for name in ["first", "volume"]]
    url = f"/v1/households/{home}/grocery-requirements/shortage-preview"
    first = client.post(url, json={"recipes": selections}).json()
    second = client.post(url, json={"recipes": list(reversed(selections))}).json()
    assert first == second
    rows = first["requirements"]
    assert [row["canonical_unit"] for row in rows] == ["g", "ml"]
    assert Decimal(rows[0]["available_quantity"]) == Decimal(150)
    assert Decimal(rows[1]["available_quantity"]) == Decimal(1000)
    assert [lot["pantry_item_id"] for lot in rows[0]["pantry_lots"]] == sorted(str(value) for value in ids)
    assert any(warning["code"] == "incompatible_units" for warning in first["warnings"])


def test_no_database_mutations_locks_or_autoflush(shortage_data, monkeypatch):
    _, sessions, home, _, recipes = shortage_data
    add_lot(shortage_data, "100")
    add_lot(shortage_data, "100", expiration=date(2000, 1, 1))
    with sessions() as session:
        def snapshot():
            return {table.name: session.execute(select(table)).all()
                    for table in Base.metadata.sorted_tables}

        before = snapshot()
        statements = []
        calls = []
        original = GroceryRequirementsService.preview

        def requirements(service, household, data):
            calls.append(household)
            return original(service, household, data)

        monkeypatch.setattr(GroceryRequirementsService, "preview", requirements)

        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement.upper())

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            session.add(GroceryList(household_id=home, name="Unflushed"))
            result = GroceryShortageService(session).preview(home, GroceryRequirementsRequest(
                recipes=[{"recipe_id": recipes["first"], "desired_servings": 4}]))
            assert result.requirements and len(session.new) == 1
            assert calls == [home]
            assert statements and all(sql.lstrip().startswith("SELECT") for sql in statements)
            assert all("FOR UPDATE" not in sql for sql in statements)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
            session.rollback()
        assert snapshot() == before
