import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from nourish_nest.food_schemas import FoodFields, RecipeFields
from nourish_nest.models import (
    Allergy,
    DietaryPreference,
    Food,
    FoodAllergen,
    FoodDietaryTagRecord,
    Household,
    HouseholdMember,
    Recipe,
    RecipeIngredient,
    RecipeInstruction,
)
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


class FoodRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, food_id: uuid.UUID) -> Food | None:
        statement = (
            select(Food)
            .options(selectinload(Food.allergens), selectinload(Food.dietary_tags))
            .where(Food.id == food_id)
        )
        return self.session.scalars(statement).one_or_none()

    def list(self, query: str | None = None) -> list[Food]:
        statement = select(Food).options(
            selectinload(Food.allergens), selectinload(Food.dietary_tags)
        )
        if query:
            statement = statement.where(Food.normalized_name.contains(normalize_name(query)))
        return list(self.session.scalars(statement.order_by(Food.normalized_name)).all())

    def create(self, data: FoodFields) -> Food:
        food = Food(
            **data.model_dump(exclude={"allergens", "dietary_tags"}),
            normalized_name=normalize_name(data.name),
        )
        food.allergens = [FoodAllergen(**item.model_dump()) for item in data.allergens]
        food.dietary_tags = [FoodDietaryTagRecord(**item.model_dump()) for item in data.dietary_tags]
        self.session.add(food)
        self.session.flush()
        return food

    def update(self, food: Food, data: FoodFields) -> Food:
        for key, value in data.model_dump(exclude={"allergens", "dietary_tags"}).items():
            setattr(food, key, value)
        food.normalized_name = normalize_name(data.name)
        food.allergens = [FoodAllergen(**item.model_dump()) for item in data.allergens]
        food.dietary_tags = [FoodDietaryTagRecord(**item.model_dump()) for item in data.dietary_tags]
        self.session.flush()
        return food


class RecipeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_for_household(self, recipe_id: uuid.UUID, household_id: uuid.UUID) -> Recipe | None:
        return self._get(select(Recipe).where(
            Recipe.id == recipe_id,
            (Recipe.household_id == household_id) | (Recipe.household_id.is_(None)),
        ))

    def list_for_household(self, household_id: uuid.UUID) -> list[Recipe]:
        statement = select(Recipe).where(
            (Recipe.household_id == household_id) | (Recipe.household_id.is_(None))
        )
        return list(self.session.scalars(self._options(statement).order_by(Recipe.name)).all())

    def create(self, household_id: uuid.UUID, data: RecipeFields) -> Recipe:
        recipe = Recipe(
            household_id=household_id,
            **data.model_dump(exclude={"ingredients", "instructions"}),
        )
        recipe.ingredients = [RecipeIngredient(**item.model_dump()) for item in data.ingredients]
        recipe.instructions = [RecipeInstruction(**item.model_dump()) for item in data.instructions]
        self.session.add(recipe)
        self.session.flush()
        return recipe

    def update(self, recipe: Recipe, data: RecipeFields) -> Recipe:
        for key, value in data.model_dump(exclude={"ingredients", "instructions"}).items():
            setattr(recipe, key, value)
        recipe.ingredients = [RecipeIngredient(**item.model_dump()) for item in data.ingredients]
        recipe.instructions = [RecipeInstruction(**item.model_dump()) for item in data.instructions]
        self.session.flush()
        return recipe

    def _options(self, statement):
        return statement.options(
            selectinload(Recipe.ingredients)
            .selectinload(RecipeIngredient.food)
            .selectinload(Food.allergens),
            selectinload(Recipe.ingredients)
            .selectinload(RecipeIngredient.food)
            .selectinload(Food.dietary_tags),
            selectinload(Recipe.instructions),
        )

    def _get(self, statement) -> Recipe | None:
        return self.session.scalars(self._options(statement)).one_or_none()


def normalize_name(name: str) -> str:
    return " ".join(name.casefold().split())