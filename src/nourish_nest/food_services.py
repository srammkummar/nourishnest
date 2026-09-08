from dataclasses import dataclass
from decimal import Decimal

from nourish_nest.food_schemas import RecipeNutritionMacros, RecipeNutritionResponse
from nourish_nest.models import Food, FoodDietaryTag, Recipe


class UnsupportedConversionError(ValueError):
    code = "unsupported_conversion"

    def __init__(self, unit: str):
        super().__init__(f"Conversion from '{unit}' is not supported")
        self.unit = unit


MASS_TO_GRAMS = {
    "g": Decimal(1),
    "gram": Decimal(1),
    "grams": Decimal(1),
    "kg": Decimal(1000),
    "kilogram": Decimal(1000),
    "kilograms": Decimal(1000),
    "oz": Decimal("28.349523125"),
    "ounce": Decimal("28.349523125"),
    "ounces": Decimal("28.349523125"),
    "lb": Decimal("453.59237"),
    "pound": Decimal("453.59237"),
    "pounds": Decimal("453.59237"),
}
VOLUME_TO_ML = {
    "ml": Decimal(1),
    "milliliter": Decimal(1),
    "milliliters": Decimal(1),
    "l": Decimal(1000),
    "liter": Decimal(1000),
    "liters": Decimal(1000),
    "cup": Decimal("236.5882365"),
    "cups": Decimal("236.5882365"),
    "tbsp": Decimal("14.7867648"),
    "tablespoon": Decimal("14.7867648"),
    "tablespoons": Decimal("14.7867648"),
    "tsp": Decimal("4.92892159"),
    "teaspoon": Decimal("4.92892159"),
    "teaspoons": Decimal("4.92892159"),
}
COUNT_UNITS = {"item", "items", "count", "piece", "pieces"}


def normalize_unit(unit: str) -> str:
    normalized = unit.strip().lower().replace("_", " ")
    aliases = {"ounce": "oz", "ounces": "oz", "pound": "lb", "pounds": "lb"}
    return aliases.get(normalized, normalized)


@dataclass(frozen=True)
class ConvertedQuantity:
    quantity: Decimal
    canonical_unit: str


def convert_quantity(quantity: Decimal, unit: str) -> ConvertedQuantity:
    normalized = normalize_unit(unit)
    if normalized in MASS_TO_GRAMS:
        return ConvertedQuantity(quantity * MASS_TO_GRAMS[normalized], "g")
    if normalized in VOLUME_TO_ML:
        return ConvertedQuantity(quantity * VOLUME_TO_ML[normalized], "ml")
    if normalized in COUNT_UNITS:
        return ConvertedQuantity(quantity, "item")
    raise UnsupportedConversionError(unit)


@dataclass(frozen=True)
class IngredientNutrition:
    factor: Decimal | None
    warning: str | None


def ingredient_factor(quantity: Decimal, unit: str, food: Food) -> IngredientNutrition:
    ingredient = convert_quantity(quantity, unit)
    serving = convert_quantity(food.serving_quantity, food.serving_unit)
    if ingredient.canonical_unit == serving.canonical_unit:
        return IngredientNutrition(ingredient.quantity / serving.quantity, None)
    if ingredient.canonical_unit == "g" and food.grams_per_serving is not None:
        return IngredientNutrition(ingredient.quantity / food.grams_per_serving, None)
    if ingredient.canonical_unit == "item" and food.grams_per_serving is not None:
        grams = ingredient.quantity * food.grams_per_serving
        return IngredientNutrition(grams / food.grams_per_serving, None)
    return IngredientNutrition(
        None,
        f"Missing gram conversion for {food.name} ({quantity} {unit}); nutrition was not included.",
    )


@dataclass
class NutritionTotals:
    calories: Decimal = Decimal(0)
    protein_g: Decimal = Decimal(0)
    carbohydrate_g: Decimal = Decimal(0)
    fat_g: Decimal = Decimal(0)
    fiber_g: Decimal = Decimal(0)
    sugar_g: Decimal = Decimal(0)
    sodium_mg: Decimal = Decimal(0)


def _macros(totals: NutritionTotals, nullable: bool = False) -> RecipeNutritionMacros:
    return RecipeNutritionMacros(
        protein_g=None if nullable else totals.protein_g,
        carbohydrate_g=None if nullable else totals.carbohydrate_g,
        fat_g=None if nullable else totals.fat_g,
        fiber_g=None if nullable else totals.fiber_g,
        sugar_g=None if nullable else totals.sugar_g,
        sodium_mg=None if nullable else totals.sodium_mg,
    )


def calculate_recipe_nutrition(recipe: Recipe) -> RecipeNutritionResponse:
    totals = NutritionTotals()
    warnings: list[str] = []
    allergens: set[str] = set()
    tags: set[FoodDietaryTag] | None = None
    incomplete = False
    for ingredient in recipe.ingredients:
        result = ingredient_factor(ingredient.quantity, ingredient.unit, ingredient.food)
        if result.warning:
            warnings.append(result.warning)
        if result.factor is None:
            continue
        food = ingredient.food
        for field in ("calories_per_serving", "protein_g", "carbohydrate_g", "fat_g"):
            if getattr(food, field) is None:
                incomplete = True
        factor = result.factor
        totals.calories += (food.calories_per_serving or Decimal(0)) * factor
        totals.protein_g += (food.protein_g or Decimal(0)) * factor
        totals.carbohydrate_g += (food.carbohydrate_g or Decimal(0)) * factor
        totals.fat_g += (food.fat_g or Decimal(0)) * factor
        totals.fiber_g += (food.fiber_g or Decimal(0)) * factor
        totals.sugar_g += (food.sugar_g or Decimal(0)) * factor
        totals.sodium_mg += (food.sodium_mg or Decimal(0)) * factor
        allergens.update(
            item.allergen
            for item in food.allergens
            if item.relationship_type.value in {"contains", "may_contain"}
        )
        food_tags = {item.tag for item in food.dietary_tags}
        tags = food_tags if tags is None else tags.intersection(food_tags)
    if incomplete:
        warnings.append("One or more ingredients have incomplete nutrition data; missing values were treated as zero.")
    per_serving = NutritionTotals(
        calories=totals.calories / recipe.servings,
        protein_g=totals.protein_g / recipe.servings,
        carbohydrate_g=totals.carbohydrate_g / recipe.servings,
        fat_g=totals.fat_g / recipe.servings,
        fiber_g=totals.fiber_g / recipe.servings,
        sugar_g=totals.sugar_g / recipe.servings,
        sodium_mg=totals.sodium_mg / recipe.servings,
    )
    return RecipeNutritionResponse(
        total_calories=totals.calories,
        total_macros=_macros(totals),
        calories_per_serving=per_serving.calories,
        macros_per_serving=_macros(per_serving),
        aggregated_allergens=sorted(allergens),
        dietary_tags=sorted(tags or set(), key=lambda item: item.value),
        warnings=warnings,
    )