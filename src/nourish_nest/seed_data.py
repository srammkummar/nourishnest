from decimal import Decimal

from sqlalchemy import select

from nourish_nest.config import get_settings
from nourish_nest.database import SessionLocal
from nourish_nest.models import (
    Food,
    FoodDietaryTagRecord,
    Recipe,
    RecipeIngredient,
    RecipeInstruction,
)

DEVELOPMENT_FOODS = [
    ("Basmati rice, cooked", "rice", 100, 130, 2.7, 28.2, 0.3, 0.4, 0.1, 1),
    ("Chapati", "chapati", 40, 120, 3.5, 18, 3, 2.5, 0.5, 2),
    ("Dal, cooked", "dal", 200, 230, 15, 36, 4, 10, 2, 3),
    ("Chickpeas, cooked", "chickpeas", 100, 164, 8.9, 27.4, 2.6, 7.6, 4.8, 4),
    ("Paneer", "paneer", 100, 265, 18.3, 6.1, 20.8, 0, 2.5, 5),
    ("Chicken breast, cooked", "chicken", 100, 165, 31, 0, 3.6, 0, 0, 6),
    ("White fish, cooked", "fish", 100, 128, 26, 0, 2.7, 0, 0, 7),
    ("Spinach, raw", "spinach", 100, 23, 2.9, 3.6, 0.4, 2.2, 0.4, 8),
    ("Mixed vegetables, cooked", "mixed vegetables", 100, 80, 3, 14, 1, 4, 5, 9),
    ("Onion", "onion", 100, 40, 1.1, 9.3, 0.1, 1.7, 4.2, 10),
    ("Tomato", "tomato", 100, 18, 0.9, 3.9, 0.2, 1.2, 2.6, 11),
    ("Olive oil", "olive oil", 10, 88, 0, 0, 10, 0, 0, 12),
    ("Plain yogurt", "yogurt", 100, 61, 3.5, 4.7, 3.3, 0, 4.7, 13),
    ("Potato, cooked", "potato", 100, 87, 1.9, 20.1, 0.1, 1.8, 0.9, 14),
    ("Cumin seed", "cumin", 5, 19, 0.9, 2.2, 1.1, 0.5, 0.1, 15),
]


RECIPE_FIXTURES = {
    "Dal Tadka": [("dal", 200), ("onion", 50), ("tomato", 80), ("olive oil", 10), ("cumin", 5)],
    "Chana Masala": [("chickpeas", 200), ("onion", 50), ("tomato", 100), ("olive oil", 10)],
    "Palak Paneer": [("spinach", 150), ("paneer", 100), ("onion", 50), ("tomato", 80)],
    "Chicken Rice Bowl": [("chicken", 150), ("rice", 150), ("spinach", 50), ("mixed vegetables", 100)],
    "Vegetable Khichdi": [("rice", 100), ("dal", 100), ("mixed vegetables", 150), ("potato", 50)],
}


def seed_development_data() -> None:
    if get_settings().env.lower() == "production":
        raise RuntimeError("Development fixtures cannot be seeded in production")
    with SessionLocal() as session:
        foods = {
            row[1]: session.scalar(select(Food).where(Food.normalized_name == row[1]))
            for row in DEVELOPMENT_FOODS
        }
        for name, normalized_name, serving_quantity, calories, protein, carbs, fat, fiber, sugar, number in DEVELOPMENT_FOODS:
            if foods[normalized_name] is None:
                food = Food(
                    name=name,
                    normalized_name=normalized_name,
                    description="Development fixture only; not authoritative production nutrition data.",
                    source_type="development_fixture",
                    serving_quantity=Decimal(str(serving_quantity)),
                    serving_unit="g",
                    grams_per_serving=Decimal(str(serving_quantity)),
                    calories_per_serving=Decimal(str(calories)),
                    protein_g=Decimal(str(protein)),
                    carbohydrate_g=Decimal(str(carbs)),
                    fat_g=Decimal(str(fat)),
                    fiber_g=Decimal(str(fiber)),
                    sugar_g=Decimal(str(sugar)),
                    sodium_mg=Decimal(0),
                )
                if normalized_name in {"rice", "chapati", "dal", "chickpeas", "paneer", "spinach", "mixed vegetables", "yogurt", "potato"}:
                    food.dietary_tags = [FoodDietaryTagRecord(tag="vegetarian")]
                session.add(food)
                foods[normalized_name] = food
        session.flush()
        for recipe_name, ingredients in RECIPE_FIXTURES.items():
            if session.scalar(select(Recipe).where(Recipe.name == recipe_name, Recipe.household_id.is_(None))):
                continue
            recipe = Recipe(
                name=recipe_name,
                description="Development fixture recipe; nutrition values are illustrative, not authoritative.",
                cuisine="Indian-inspired",
                servings=Decimal(2),
                source="development-fixture",
                household_id=None,
            )
            recipe.ingredients = [
                RecipeIngredient(food=foods[food_key], quantity=Decimal(str(quantity)), unit="g", display_order=index)
                for index, (food_key, quantity) in enumerate(ingredients)
            ]
            recipe.instructions = [
                RecipeInstruction(step_number=1, instruction="Combine ingredients using the preparation method appropriate for the dish.")
            ]
            session.add(recipe)
        session.commit()


if __name__ == "__main__":
    seed_development_data()
    print("Development fixtures seeded")