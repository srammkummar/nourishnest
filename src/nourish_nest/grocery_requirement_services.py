import uuid
from decimal import Decimal, localcontext

from sqlalchemy.orm import Session

from nourish_nest.food_services import UnsupportedConversionError, convert_quantity
from nourish_nest.grocery_requirement_schemas import (
    GroceryRequirement,
    GroceryRequirementsRequest,
    GroceryRequirementsResponse,
    RecipeContribution,
    RequirementWarning,
)
from nourish_nest.repositories import HouseholdRepository, RecipeRepository
from nourish_nest.services import NotFoundError


class GroceryRequirementsService:
    def __init__(self, session: Session):
        self.session = session
        self.households = HouseholdRepository(session)
        self.recipes = RecipeRepository(session)

    def preview(self, household_id: uuid.UUID, data: GroceryRequirementsRequest):
        # Disable autoflush as well as explicit writes: preview never persists pending work.
        with self.session.no_autoflush, localcontext() as context:
            context.prec = 28
            context.rounding = "ROUND_HALF_EVEN"
            if self.households.get(household_id) is None:
                raise NotFoundError("Household not found")
            selections = sorted(data.recipes, key=lambda selection: selection.recipe_id)
            groups: dict[tuple[uuid.UUID, str], GroceryRequirement] = {}
            warnings: list[RequirementWarning] = []
            for selection in selections:
                recipe = self.recipes.get_for_household(selection.recipe_id, household_id)
                if recipe is None:
                    raise NotFoundError("Recipe not found")
                for ingredient in sorted(recipe.ingredients, key=lambda row: (row.display_order, row.id)):
                    scaled = ingredient.quantity * selection.desired_servings / recipe.servings
                    try:
                        converted = convert_quantity(scaled, ingredient.unit)
                    except UnsupportedConversionError as exc:
                        warnings.append(RequirementWarning(
                            code="unsupported_conversion", message=str(exc),
                            food_id=ingredient.food_id, recipe_id=recipe.id,
                            ingredient_id=ingredient.id, scaled_quantity=scaled,
                            original_unit=ingredient.unit,
                        ))
                        continue
                    key = (ingredient.food_id, converted.canonical_unit)
                    if key not in groups:
                        groups[key] = GroceryRequirement(
                            food_id=ingredient.food_id, food_name=ingredient.food.name,
                            required_quantity=Decimal(0), canonical_unit=converted.canonical_unit,
                            sources=[],
                        )
                    requirement = groups[key]
                    requirement.required_quantity += converted.quantity
                    requirement.sources.append(RecipeContribution(
                        recipe_id=recipe.id, recipe_name=recipe.name, ingredient_id=ingredient.id,
                        scaled_quantity=scaled, original_unit=ingredient.unit,
                        required_quantity=converted.quantity,
                    ))
            requirements = sorted(groups.values(), key=lambda row: (
                row.food_name.casefold(), row.food_id, row.canonical_unit
            ))
            units_by_food: dict[uuid.UUID, set[str]] = {}
            for requirement in requirements:
                units_by_food.setdefault(requirement.food_id, set()).add(requirement.canonical_unit)
            for food_id, units in units_by_food.items():
                if len(units) > 1:
                    warnings.append(RequirementWarning(
                        code="incompatible_units", food_id=food_id, canonical_units=sorted(units),
                        message="Requirements remain separate across dimensions; no density or count conversion was inferred.",
                    ))
            return GroceryRequirementsResponse(
                household_id=household_id, recipes=selections,
                requirements=requirements, warnings=warnings,
            )
