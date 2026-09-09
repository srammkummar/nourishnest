from io import StringIO
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, select
from sqlalchemy.orm import Session

from alembic import command
from nourish_nest.config import get_settings
from nourish_nest.database import Base
from nourish_nest.models import Household, HouseholdMember, utc_now
from nourish_nest.schemas import MemberUpdate
from nourish_nest.services import HouseholdService, StaleMemberVersionError

FIELDS = {
    "name": "Alex",
    "age": 35,
    "sex": "female",
    "height_cm": 165,
    "weight_kg": 68,
    "activity_level": "moderate",
    "goal": "maintain",
    "weekly_goal_kg": 0,
    "meals_per_day": 3,
}


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_two_session_stale_mutations_roll_back(tmp_path, operation):
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as setup:
        home = Household(name="Home")
        member = HouseholdMember(household=home, **FIELDS)
        setup.add(member)
        setup.commit()
        home_id, member_id = home.id, member.id
    with Session(engine) as first, Session(engine) as second:
        one, two = HouseholdService(first), HouseholdService(second)
        initial = two.get_member(home_id, member_id)
        assert initial.version == 1
        one.update_member(
            home_id,
            member_id,
            MemberUpdate(
                **FIELDS, expected_version=1, allergies=[{"allergen": "Milk", "severity": "mild"}]
            ),
        )
        with pytest.raises(StaleMemberVersionError):
            if operation == "update":
                two.update_member(
                    home_id,
                    member_id,
                    MemberUpdate(**{**FIELDS, "name": "Stale"}, expected_version=1),
                )
            else:
                two.delete_member(home_id, member_id, 1)
        # The session is usable after rollback, and dependent data survives stale deletion.
        saved = two.get_member(home_id, member_id)
        assert saved.version == 2 and saved.name == "Alex"
        assert saved.allergies[0].allergen == "Milk"
    engine.dispose()


def test_forced_commit_failure_rolls_back_parent_and_children(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'rollback.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        member = HouseholdMember(household=Household(name="Home"), **FIELDS)
        session.add(member)
        session.commit()
        home_id, member_id = member.household_id, member.id

        def fail():
            raise RuntimeError("forced commit failure")

        monkeypatch.setattr(session, "commit", fail)
        with pytest.raises(RuntimeError):
            HouseholdService(session).update_member(
                home_id,
                member_id,
                MemberUpdate(
                    **FIELDS,
                    expected_version=1,
                    allergies=[{"allergen": "Milk", "severity": "mild"}],
                ),
            )
        saved = HouseholdService(session).get_member(home_id, member_id)
        assert saved.version == 1 and saved.allergies == []
    engine.dispose()


def test_member_migration_preserves_existing_rows(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260909_0007")
    engine = create_engine(url)
    old = MetaData()
    old.reflect(engine)
    home_id, member_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            old.tables["households"]
            .insert()
            .values(
                id=home_id.hex,
                name="Existing",
                timezone="UTC",
                currency="USD",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        connection.execute(
            old.tables["household_members"]
            .insert()
            .values(
                id=member_id.hex,
                household_id=home_id.hex,
                **FIELDS,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
    command.upgrade(config, "head")
    with Session(engine) as session:
        assert session.get(HouseholdMember, member_id).version == 1
        HouseholdService(session).update_member(
            home_id, member_id, MemberUpdate(**FIELDS, expected_version=1)
        )
        assert session.scalar(select(HouseholdMember.version)) == 2
    command.downgrade(config, "-1")
    assert "version" not in {c["name"] for c in inspect(engine).get_columns("household_members")}
    command.upgrade(config, "head")
    with Session(engine) as session:
        saved = session.get(HouseholdMember, member_id)
        assert saved.name == "Alex" and saved.version == 1
    engine.dispose()


def test_member_migration_offline_postgresql(monkeypatch):
    monkeypatch.setattr(get_settings(), "database_url", "postgresql://test:test@localhost/test")
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "20260909_0007:20260909_0008", sql=True)
    command.downgrade(config, "20260909_0008:20260909_0007", sql=True)
    ddl = output.getvalue()
    assert "ADD COLUMN version INTEGER DEFAULT '1' NOT NULL" in ddl
    assert "DROP COLUMN version" in ddl
