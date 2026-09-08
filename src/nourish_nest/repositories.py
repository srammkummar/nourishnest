import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from nourish_nest.models import Allergy, DietaryPreference, Household, HouseholdMember
from nourish_nest.schemas import MemberFields


class HouseholdRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, name: str, timezone: str, currency: str) -> Household:
        household = Household(name=name, timezone=timezone, currency=currency)
        self.session.add(household)
        self.session.flush()
        return household

    def get(self, household_id: uuid.UUID) -> Household | None:
        return self.session.get(Household, household_id)


class MemberRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, member_id: uuid.UUID) -> HouseholdMember | None:
        statement = (
            select(HouseholdMember)
            .options(
                selectinload(HouseholdMember.dietary_preferences),
                selectinload(HouseholdMember.allergies),
            )
            .where(HouseholdMember.id == member_id)
        )
        return self.session.scalars(statement).one_or_none()

    def list_for_household(self, household_id: uuid.UUID) -> list[HouseholdMember]:
        statement = (
            select(HouseholdMember)
            .options(
                selectinload(HouseholdMember.dietary_preferences),
                selectinload(HouseholdMember.allergies),
            )
            .where(HouseholdMember.household_id == household_id)
            .order_by(HouseholdMember.created_at)
        )
        return list(self.session.scalars(statement).all())

    def create(self, household_id: uuid.UUID, data: MemberFields) -> HouseholdMember:
        values = data.model_dump(exclude={"dietary_preferences", "allergies"})
        member = HouseholdMember(household_id=household_id, **values)
        member.dietary_preferences = [
            DietaryPreference(**item.model_dump()) for item in data.dietary_preferences
        ]
        member.allergies = [Allergy(**item.model_dump()) for item in data.allergies]
        self.session.add(member)
        self.session.flush()
        return member

    def update(self, member: HouseholdMember, data: MemberFields) -> HouseholdMember:
        values = data.model_dump(exclude={"dietary_preferences", "allergies"})
        for key, value in values.items():
            setattr(member, key, value)
        member.dietary_preferences = [
            DietaryPreference(**item.model_dump()) for item in data.dietary_preferences
        ]
        member.allergies = [Allergy(**item.model_dump()) for item in data.allergies]
        self.session.flush()
        return member