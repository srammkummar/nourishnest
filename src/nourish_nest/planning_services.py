"""Read-only recommendations. Safety checks precede quantity conversion and scoring."""

from datetime import timedelta
from decimal import Decimal, localcontext

from nourish_nest.config import get_settings
from nourish_nest.food_services import UnsupportedConversionError, calculate_recipe_nutrition
from nourish_nest.grocery_requirement_schemas import GroceryRequirementsRequest
from nourish_nest.grocery_shortage_services import GroceryShortageService
from nourish_nest.models import utc_now
from nourish_nest.planning_contracts import (
    ExpiringFood,
    FoodNeed,
    PlanningWarning,
    RecipeRecommendation,
    RecommendationResponse,
    RecommendationScore,
    ServingNutrition,
)
from nourish_nest.repositories import HouseholdRepository, MemberRepository, RecipeRepository
from nourish_nest.services import NotFoundError

ZERO = Decimal(0)


def normalized(value):
    return " ".join(value.casefold().split())


def dietary_check(recipe, member):
    """Require affirmative structured tags; never infer safety from food names."""
    warnings = []
    allergies = {normalized(a.allergen) for a in member.allergies} if member else set()
    for ingredient in recipe.ingredients:
        for allergen in ingredient.food.allergens:
            matches = normalized(allergen.allergen) in allergies
            if matches and allergen.relationship_type == "contains":
                return False, []
            if allergen.relationship_type == "may_contain":
                warnings.append(
                    PlanningWarning(
                        code="may_contain",
                        message=f"{ingredient.food.name} may contain {allergen.allergen}.",
                    )
                )
    for preference in member.dietary_preferences if member else []:
        tag = preference.preference_type.value.replace("-", "_")
        if tag == "custom":
            warnings.append(
                PlanningWarning(
                    code="unverified_dietary_preference",
                    message=f"Custom preference was not automatically checked: {preference.value}.",
                )
            )
        elif any(tag not in {t.tag.value for t in i.food.dietary_tags} for i in recipe.ingredients):
            return False, []
    return True, warnings


def serving_nutrition(recipe):
    try:
        result = calculate_recipe_nutrition(recipe)
    except UnsupportedConversionError as error:
        return ServingNutrition(warnings=[f"Nutrition is unavailable: {error}."])
    return ServingNutrition(
        calories=result.calories_per_serving,
        protein_g=result.macros_per_serving.protein_g,
        carbohydrate_g=result.macros_per_serving.carbohydrate_g,
        fat_g=result.macros_per_serving.fat_g,
        warnings=result.warnings,
    )


class RecommendationService:
    def __init__(self, session):
        self.session = session

    def recommend(self, household_id, data):
        with self.session.no_autoflush, localcontext() as context:
            context.prec = 28
            context.rounding = "ROUND_HALF_EVEN"
            if HouseholdRepository(self.session).get(household_id) is None:
                raise NotFoundError("Household not found")
            member = None
            if data.member_id:
                member = MemberRepository(self.session).get(household_id, data.member_id)
                if member is None:
                    raise NotFoundError("Member not found")
            as_of = utc_now()
            result = []
            for recipe in RecipeRepository(self.session).list_for_household(household_id):
                if data.cuisine and normalized(recipe.cuisine or "") != normalized(data.cuisine):
                    continue
                if (
                    data.maximum_cooking_minutes is not None
                    and recipe.cooking_minutes > data.maximum_cooking_minutes
                ):
                    continue
                allowed, warnings = dietary_check(recipe, member)
                if not allowed:
                    continue
                preview = GroceryShortageService(self.session).preview(
                    household_id,
                    GroceryRequirementsRequest(
                        recipes=[{"recipe_id": recipe.id, "desired_servings": recipe.servings}]
                    ),
                )
                warnings.extend(
                    PlanningWarning(code=w.code, message=w.message) for w in preview.warnings
                )
                needs, expiring, fractions, expiry_fractions = [], [], [], []
                expiry_end = preview.calculation_as_of.date() + timedelta(
                    days=get_settings().pantry_expiring_soon_days
                )
                for requirement in preview.requirements:
                    required = requirement.required_quantity
                    needs.append(
                        FoodNeed(
                            food_id=requirement.food_id,
                            food_name=requirement.food_name,
                            required_quantity=required,
                            available_quantity=requirement.available_quantity,
                            missing_quantity=requirement.shortage_quantity,
                            unit=requirement.canonical_unit,
                        )
                    )
                    fractions.append(min(requirement.available_quantity / required, Decimal(1)))
                    lots = [
                        lot
                        for lot in requirement.pantry_lots
                        if lot.expiration_date is not None
                        and preview.calculation_as_of.date() <= lot.expiration_date <= expiry_end
                    ]
                    expiring_quantity = min(
                        sum((lot.available_quantity for lot in lots), ZERO), required
                    )
                    expiry_fractions.append(expiring_quantity / required)
                    if expiring_quantity > 0:
                        expiring.append(
                            ExpiringFood(
                                food_id=requirement.food_id,
                                food_name=requirement.food_name,
                                quantity=expiring_quantity,
                                unit=requirement.canonical_unit,
                                earliest_expiration=min(lot.expiration_date for lot in lots),
                            )
                        )
                # Unsupported requirements are unresolved needs, never silently "covered".
                unknown = {}
                food_names = {i.food_id: i.food.name for i in recipe.ingredients}
                for warning in preview.warnings:
                    if warning.code != "unsupported_conversion":
                        continue
                    key = (warning.food_id, warning.original_unit)
                    unknown[key] = unknown.get(key, ZERO) + warning.scaled_quantity
                for (food_id, unit), quantity in unknown.items():
                    needs.append(
                        FoodNeed(
                            food_id=food_id,
                            food_name=food_names[food_id],
                            required_quantity=quantity,
                            available_quantity=ZERO,
                            missing_quantity=quantity,
                            unit=unit,
                            conversion_supported=False,
                        )
                    )
                    fractions.append(ZERO)
                    expiry_fractions.append(ZERO)
                missing = [need for need in needs if need.missing_quantity > 0]
                missing_count = len({need.food_id for need in missing})
                if missing_count > data.maximum_missing_ingredients:
                    continue
                if not fractions:
                    warnings.append(
                        PlanningWarning(
                            code="empty_recipe", message="Recipe has no calculable ingredients."
                        )
                    )
                coverage = sum(fractions, ZERO) / max(len(fractions), 1)
                expiry = sum(expiry_fractions, ZERO) / max(len(fractions), 1)
                score = RecommendationScore(
                    coverage_points=coverage * 80,
                    expiring_points=expiry * 20,
                    total=coverage * 80 + expiry * 20,
                )
                classification = (
                    "Ready to make"
                    if coverage == 1 and not missing
                    else "Missing 1–2 ingredients"
                    if 1 <= missing_count <= 2
                    else "Needs shopping"
                )
                needs.sort(key=lambda n: (n.food_name.casefold(), n.food_id, n.unit))
                missing = [need for need in needs if need.missing_quantity > 0]
                result.append(
                    RecipeRecommendation(
                        recipe_id=recipe.id,
                        recipe_name=recipe.name,
                        system_recipe=recipe.household_id is None,
                        servings=recipe.servings,
                        cuisine=recipe.cuisine,
                        preparation_minutes=recipe.preparation_minutes,
                        cooking_minutes=recipe.cooking_minutes,
                        coverage_percentage=coverage * 100,
                        classification=classification,
                        missing_ingredient_count=missing_count,
                        requirements=needs,
                        missing_ingredients=missing,
                        expiring_ingredients=expiring,
                        score=score,
                        explanation=f"Pantry covers {coverage * 100:.1f}% of ingredient quantities for {recipe.servings} servings; {missing_count} foods need shopping. Expiring stock contributes {score.expiring_points:.1f} of 20 bonus points.",
                        nutrition_per_serving=serving_nutrition(recipe),
                        warnings=warnings,
                    )
                )
            result.sort(
                key=lambda r: (
                    -r.score.total,
                    -r.coverage_percentage,
                    r.recipe_name.casefold(),
                    r.recipe_id,
                )
            )
            return RecommendationResponse(
                household_id=household_id,
                member_id=data.member_id,
                recommendations=result[: data.limit],
                calculation_as_of=as_of,
                warnings=[
                    PlanningWarning(
                        code="stored_data_only",
                        message=(
                            "Allergen matching uses case-insensitive stored names, not synonyms. Check ingredient labels; missing allergen data is not a safety guarantee. Structured dietary preferences require matching tags on every ingredient; custom preferences cannot be verified."
                            if member
                            else "Household planning does not apply individual allergy or dietary filters. Select a member for personal filters."
                        ),
                    )
                ],
            )
