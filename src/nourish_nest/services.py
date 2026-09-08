import uuid

from sqlalchemy.orm import Session

from nourish_nest.domain import NutritionProfile
from nourish_nest.models import Household, HouseholdMember
from nourish_nest.nutrition import calculate_nutrition_plan
from nourish_nest.repositories import HouseholdRepository, MemberRepository
from nourish_nest.schemas import HouseholdCreate, MemberFields


class NotFoundError(LookupError):
    pass


class HouseholdService:
    def __init__(self, session: Session):
        self.session = session
        self.households = HouseholdRepository(session)
        self.members = MemberRepository(session)

    def create_household(self, data: HouseholdCreate) -> Household:
        household = self.households.create(data.name, data.timezone, data.currency)
        self.session.commit()
        self.session.refresh(household)
        return household

    def get_household(self, household_id: uuid.UUID) -> Household:
        household = self.households.get(household_id)
        if household is None:
            raise NotFoundError("Household not found")
        return household

    def delete_household(self, household_id: uuid.UUID) -> None:
        household = self.get_household(household_id)
        self.session.delete(household)
        self.session.commit()

    def create_member(self, household_id: uuid.UUID, data: MemberFields) -> HouseholdMember:
        self.get_household(household_id)
        member = self.members.create(household_id, data)
        self.session.commit()
        self.session.refresh(member)
        return member

    def list_members(self, household_id: uuid.UUID) -> list[HouseholdMember]:
        self.get_household(household_id)
        return self.members.list_for_household(household_id)

    def get_member(self, member_id: uuid.UUID) -> HouseholdMember:
        member = self.members.get(member_id)
        if member is None:
            raise NotFoundError("Member not found")
        return member

    def update_member(self, member_id: uuid.UUID, data: MemberFields) -> HouseholdMember:
        member = self.get_member(member_id)
        updated = self.members.update(member, data)
        self.session.commit()
        self.session.refresh(updated)
        return updated

    def delete_member(self, member_id: uuid.UUID) -> None:
        member = self.get_member(member_id)
        self.session.delete(member)
        self.session.commit()

    def calculate_member_nutrition(self, member_id: uuid.UUID):
        member = self.get_member(member_id)
        profile = NutritionProfile(
            age=member.age,
            sex=member.sex,
            height_cm=member.height_cm,
            weight_kg=member.weight_kg,
            activity_level=member.activity_level,
            goal=member.goal,
            weekly_goal_kg=member.weekly_goal_kg,
            meals_per_day=member.meals_per_day,
        )
        return calculate_nutrition_plan(profile)