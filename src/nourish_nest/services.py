import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from nourish_nest.domain import NutritionProfile
from nourish_nest.food_schemas import FoodFields, RecipeFields, RecipeUpdate
from nourish_nest.food_services import calculate_recipe_nutrition
from nourish_nest.models import (
    Food,
    FoodSourceType,
    GroceryItemRecipeSource,
    Household,
    HouseholdMember,
    Recipe,
    RecipeCreationRecord,
)
from nourish_nest.nutrition import calculate_nutrition_plan
from nourish_nest.providers import FoodDataProvider, ProviderFood
from nourish_nest.recipe_idempotency import canonical_recipe_hash
from nourish_nest.repositories import (
    FoodRepository,
    HouseholdRepository,
    MemberRepository,
    RecipeRepository,
)
from nourish_nest.schemas import HouseholdCreate, MemberFields, MemberUpdate


class NotFoundError(LookupError):
    pass


class ForbiddenError(PermissionError):
    pass


class ConflictError(RuntimeError):
    pass


class StaleMemberVersionError(ConflictError):
    pass


class RecipeMutationError(ConflictError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


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

    def list_households(self) -> list[Household]:
        return self.households.list()

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

    def get_member(self, household_id: uuid.UUID, member_id: uuid.UUID) -> HouseholdMember:
        self.get_household(household_id)
        member = self.members.get(household_id, member_id)
        if member is None:
            raise NotFoundError("Member not found")
        return member

    def update_member(
        self, household_id: uuid.UUID, member_id: uuid.UUID, data: MemberUpdate
    ) -> HouseholdMember:
        try:
            member = self.get_member(household_id, member_id)
            if member.version != data.expected_version:
                raise StaleMemberVersionError("Member changed. Refresh before trying again.")
            # Force a parent UPDATE even when only preferences/allergies changed.
            member.version += 1
            updated = self.members.update(member, data)
            self.session.commit()
            self.session.refresh(updated)
            return updated
        except StaleDataError as exc:
            self.session.rollback()
            raise StaleMemberVersionError("Member changed. Refresh before trying again.") from exc
        except Exception:
            self.session.rollback()
            raise

    def delete_member(
        self, household_id: uuid.UUID, member_id: uuid.UUID, expected_version: int
    ) -> None:
        try:
            member = self.get_member(household_id, member_id)
            if member.version != expected_version:
                raise StaleMemberVersionError("Member changed. Refresh before trying again.")
            self.session.delete(member)
            self.session.commit()
        except StaleDataError as exc:
            self.session.rollback()
            raise StaleMemberVersionError("Member changed. Refresh before trying again.") from exc
        except Exception:
            self.session.rollback()
            raise

    def calculate_member_nutrition(self, household_id: uuid.UUID, member_id: uuid.UUID):
        member = self.get_member(household_id, member_id)
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

    def import_usda(self, provider: FoodDataProvider, fdc_id: int) -> Food:
        external_id = str(fdc_id)
        if self.foods.get_by_source("usda_fdc", external_id) is not None:
            raise ConflictError("USDA food has already been imported")
        provider_food = provider.get_food(fdc_id)
        food = self.foods.create(self._provider_fields(provider_food))
        self._apply_provider_metadata(food, provider_food)
        self.session.commit()
        return self.foods.get(food.id)

    def refresh_usda(self, provider: FoodDataProvider, food_id: uuid.UUID) -> Food:
        food = self.get(food_id)
        if (
            food.source_type != FoodSourceType.EXTERNAL
            or food.source_provider != "usda_fdc"
            or not food.external_source_identifier
        ):
            raise ConflictError("Food is not a USDA import")
        provider_food = provider.get_food(int(food.external_source_identifier))
        self.foods.update(food, self._provider_fields(provider_food))
        self._apply_provider_metadata(food, provider_food)
        self.session.commit()
        return self.foods.get(food.id)

    @staticmethod
    def _provider_fields(provider_food: ProviderFood) -> FoodFields:
        return FoodFields(
            name=provider_food.description,
            brand=provider_food.brand_name or provider_food.brand_owner,
            description=provider_food.description,
            source_type=FoodSourceType.EXTERNAL,
            source_provider="usda_fdc",
            external_source_identifier=str(provider_food.fdc_id),
            serving_quantity=provider_food.serving_quantity,
            serving_unit=provider_food.serving_unit,
            grams_per_serving=provider_food.grams_per_serving,
            calories_per_serving=provider_food.calories_per_serving,
            protein_g=provider_food.protein_g,
            carbohydrate_g=provider_food.carbohydrate_g,
            fat_g=provider_food.fat_g,
            fiber_g=provider_food.fiber_g,
            sugar_g=provider_food.sugar_g,
            sodium_mg=provider_food.sodium_mg,
        )

    @staticmethod
    def _apply_provider_metadata(food: Food, provider_food: ProviderFood) -> None:
        food.source_data_type = provider_food.data_type
        food.source_attribution = provider_food.source_attribution
        food.source_retrieved_at = provider_food.retrieved_at


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

    def _replay(self, household_id: uuid.UUID, key: str, request_hash: str) -> Recipe | None:
        record = self.recipes.creation_record(household_id, key)
        if record is None:
            return None
        if record.request_hash != request_hash:
            raise RecipeMutationError(
                "idempotency_conflict", "Idempotency key was used for a different recipe request."
            )
        if record.recipe_id is None:
            raise RecipeMutationError(
                "idempotency_result_deleted",
                "The recipe created with this key was deleted. Reset the form to start a new creation.",
            )
        return self.get(household_id, record.recipe_id)

    def create(self, household_id: uuid.UUID, data: RecipeFields, idempotency_key: str) -> Recipe:
        request_hash = canonical_recipe_hash(data)
        try:
            if not idempotency_key.strip() or len(idempotency_key) > 128:
                raise RecipeMutationError(
                    "invalid_request",
                    "Idempotency-Key must be nonblank and at most 128 characters.",
                )
            self._household(household_id)
            existing = self._replay(household_id, idempotency_key, request_hash)
            if existing is not None:
                return existing
            self._validate_foods(data)
            recipe = self.recipes.create(household_id, data)
            self.session.add(
                RecipeCreationRecord(
                    household_id=household_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    recipe_id=recipe.id,
                )
            )
            self.session.flush()
            result = self.get(household_id, recipe.id)
            self.session.commit()
            return result
        except IntegrityError:
            self.session.rollback()
            # A concurrent winner is resolved only after the database rejects this write.
            try:
                existing = self._replay(household_id, idempotency_key, request_hash)
                if existing is not None:
                    return existing
                raise
            except Exception:
                self.session.rollback()
                raise
        except Exception:
            self.session.rollback()
            raise

    def list(self, household_id: uuid.UUID) -> list[Recipe]:
        self._household(household_id)
        return self.recipes.list_for_household(household_id)

    def get(self, household_id: uuid.UUID, recipe_id: uuid.UUID) -> Recipe:
        self._household(household_id)
        recipe = self.recipes.get_for_household(recipe_id, household_id)
        if recipe is None:
            raise NotFoundError("Recipe not found")
        return recipe

    def update(self, household_id: uuid.UUID, recipe_id: uuid.UUID, data: RecipeUpdate) -> Recipe:
        try:
            recipe = self.get(household_id, recipe_id)
            if recipe.household_id is None:
                raise ForbiddenError("System recipes cannot be edited through household endpoints")
            if recipe.version != data.expected_version:
                raise RecipeMutationError(
                    "stale_recipe_version",
                    "Recipe changed. Cancel editing and refresh recipes before trying again.",
                )
            self._validate_foods(data)
            recipe.version += 1  # Also protects ingredient/instruction-only changes.
            self.recipes.update(recipe, data)
            result = self.get(household_id, recipe_id)
            self.session.commit()
            return result
        except StaleDataError as exc:
            self.session.rollback()
            raise RecipeMutationError(
                "stale_recipe_version", "Recipe changed. Refresh recipes before trying again."
            ) from exc
        except Exception:
            self.session.rollback()
            raise

    def delete(self, household_id: uuid.UUID, recipe_id: uuid.UUID, expected_version: int) -> None:
        try:
            recipe = self.get(household_id, recipe_id)
            if recipe.household_id is None:
                raise ForbiddenError("System recipes cannot be edited through household endpoints")
            if recipe.version != expected_version:
                raise RecipeMutationError(
                    "stale_recipe_version", "Recipe changed. Refresh recipes before trying again."
                )
            if self.session.scalar(
                select(GroceryItemRecipeSource.id)
                .where(GroceryItemRecipeSource.recipe_id == recipe_id)
                .limit(1)
            ):
                raise RecipeMutationError(
                    "recipe_in_use",
                    "Recipe is referenced by grocery source history and cannot be deleted.",
                )
            # Let database cascades run only after the versioned parent delete succeeds.
            self.session.expire(recipe, ["ingredients", "instructions"])
            self.session.delete(recipe)
            self.session.commit()
        except StaleDataError as exc:
            self.session.rollback()
            raise RecipeMutationError(
                "stale_recipe_version", "Recipe changed. Refresh recipes before trying again."
            ) from exc
        except IntegrityError as exc:
            self.session.rollback()
            # The foreign key still protects a lineage row inserted after the pre-check.
            raise RecipeMutationError(
                "recipe_in_use", "Recipe is referenced by other records and cannot be deleted."
            ) from exc
        except Exception:
            self.session.rollback()
            raise

    def nutrition(self, household_id: uuid.UUID, recipe_id: uuid.UUID):
        return calculate_recipe_nutrition(self.get(household_id, recipe_id))
