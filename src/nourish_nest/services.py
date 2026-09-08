import uuid

from sqlalchemy.orm import Session

from nourish_nest.domain import NutritionProfile
from nourish_nest.food_schemas import FoodFields, RecipeFields
from nourish_nest.food_services import calculate_recipe_nutrition
from nourish_nest.models import Food, Household, HouseholdMember, Recipe
from nourish_nest.nutrition import calculate_nutrition_plan
from nourish_nest.repositories import (
    FoodRepository,
    HouseholdRepository,
    MemberRepository,
    RecipeRepository,
)
from nourish_nest.schemas import HouseholdCreate, MemberFields


class NotFoundError(LookupError):
    pass


class ForbiddenError(PermissionError):
    pass


class ConflictError(RuntimeError):
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


class FoodService:
    def __init__(self, session: Session):
        self.session = session
        self.foods = FoodRepository(session)

    def create(self, data: FoodFields) -> Food:
        food = self.foods.create(data)
        self.session.commit()
        return self.foods.get(food.id)

    def list(self, query: str | None = None) -> list[Food]:
        return self.foods.list(query)

    def get(self, food_id: uuid.UUID) -> Food:
        food = self.foods.get(food_id)
        if food is None:
            raise NotFoundError("Food not found")
        return food

    def update(self, food_id: uuid.UUID, data: FoodFields) -> Food:
        food = self.get(food_id)
        self.foods.update(food, data)
        self.session.commit()
        return self.foods.get(food_id)

    def delete(self, food_id: uuid.UUID) -> None:
        food = self.get(food_id)
        if food.recipe_ingredients:
            raise ConflictError("Food is used by a recipe and cannot be deleted")
        self.session.delete(food)
        self.session.commit()


class RecipeService:
    def __init__(self, session: Session):
        self.session = session
        self.households = HouseholdRepository(session)
        self.foods = FoodRepository(session)
        self.recipes = RecipeRepository(session)

    def _household(self, household_id: uuid.UUID) -> Household:
        household = self.households.get(household_id)
        if household is None:
            raise NotFoundError("Household not found")
        return household

    def _validate_foods(self, data: RecipeFields) -> None:
        for ingredient in data.ingredients:
            if self.foods.get(ingredient.food_id) is None:
                raise NotFoundError("Food not found")

    def create(self, household_id: uuid.UUID, data: RecipeFields) -> Recipe:
        self._household(household_id)
        self._validate_foods(data)
        recipe = self.recipes.create(household_id, data)
        self.session.commit()
        return self.recipes.get_for_household(recipe.id, household_id)

    def list(self, household_id: uuid.UUID) -> list[Recipe]:
        self._household(household_id)
        return self.recipes.list_for_household(household_id)

    def get(self, household_id: uuid.UUID, recipe_id: uuid.UUID) -> Recipe:
        self._household(household_id)
        recipe = self.recipes.get_for_household(recipe_id, household_id)
        if recipe is None:
            raise NotFoundError("Recipe not found")
        return recipe

    def update(self, household_id: uuid.UUID, recipe_id: uuid.UUID, data: RecipeFields) -> Recipe:
        recipe = self.get(household_id, recipe_id)
        if recipe.household_id is None:
            raise ForbiddenError("System recipes cannot be edited through household endpoints")
        self._validate_foods(data)
        self.recipes.update(recipe, data)
        self.session.commit()
        return self.recipes.get_for_household(recipe_id, household_id)

    def delete(self, household_id: uuid.UUID, recipe_id: uuid.UUID) -> None:
        recipe = self.get(household_id, recipe_id)
        if recipe.household_id is None:
            raise ForbiddenError("System recipes cannot be edited through household endpoints")
        self.session.delete(recipe)
        self.session.commit()

    def nutrition(self, household_id: uuid.UUID, recipe_id: uuid.UUID):
        return calculate_recipe_nutrition(self.get(household_id, recipe_id))