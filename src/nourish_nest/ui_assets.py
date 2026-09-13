"""Deterministic, presentation-only image registry; no network or application data reads."""

import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "static" / "assets"
IMAGES = {
    "kitchen": ("hero/kitchen.webp", "A family preparing fresh food together in a bright kitchen"),
    "ingredients": ("hero/ingredients.webp", "Fresh vegetables, herbs and rice ready for cooking"),
    "pantry": ("pantry/shelves.webp", "Organized pantry shelves with ingredients in glass jars"),
    "grocery": ("grocery/basket.webp", "A grocery basket filled with fresh vegetables"),
    "nutrition": ("nutrition/workspace.svg", "A nutrition-planning notebook beside a plate and water"),
    "assistant": ("assistant/planning.svg", "A calm meal-planning workspace with a notebook, bowl and leaves"),
    "meal-prep": ("hero/meal-prep.webp", "Portioned meals with beans and vegetables prepared for the week"),
    "rice": ("recipes/rice.webp", "Vegetable fried rice with cucumber and lime; rice dish inspiration"),
    "pasta": ("recipes/pasta.webp", "Plated vegan pasta and tomato salad; pasta serving inspiration"),
    "soup": ("recipes/soup.webp", "A bowl of vegetable soup; soup and stew inspiration"),
    "breakfast": ("recipes/breakfast.webp", "Oatmeal bowls topped with fresh fruit; breakfast inspiration"),
    "chicken": ("recipes/chicken.webp", "Grilled spiced chicken on a plate; chicken serving inspiration"),
    "seafood": ("recipes/seafood.webp", "Salmon with pasta and vegetables; seafood serving inspiration"),
    "curry": ("recipes/curry.webp", "Plant-based lentil curry with rice and herbs; curry inspiration"),
    "legumes": ("recipes/legumes.webp", "Cooked lentils beside rice; bean and lentil dish inspiration"),
    "vegetables": ("recipes/vegetables.webp", "A vegetable meal bowl with chickpeas, kale and lemon"),
    "salad": ("recipes/salad.webp", "A fresh vegetable salad bowl with avocado and lime"),
}
# A plant-based meal is safe even when a recipe's dietary information is absent.
IMAGES["recipe"] = IMAGES["vegetables"]
PAGE_IMAGES = {
    "dashboard": "vegetables", "recipes": "pasta", "meal planner": "meal-prep",
    "pantry": "pantry", "grocery lists": "grocery", "nutrition": "nutrition",
    "household": "kitchen", "ai assistant": "assistant",
}
EXPLICIT_RECIPES = {
    "dal tadka": "legumes", "chana masala": "legumes", "palak paneer": "curry",
    "chicken rice bowl": "chicken", "vegetable khichdi": "rice",
}
KEYWORDS = (
    ("chicken", "chicken poultry"),
    ("seafood", "seafood fish salmon tuna shrimp prawns cod tilapia trout sardines"),
    ("rice", "rice biryani pulao pilaf khichdi risotto"),
    ("pasta", "pasta spaghetti penne fusilli macaroni lasagna linguine"),
    ("salad", "salad slaw"),
    ("soup", "soup stew broth chowder"),
    ("legumes", "bean beans lentil lentils dal dhal daal chickpea chickpeas chana rajma"),
    ("curry", "curry masala palak saag"),
    ("breakfast", "breakfast oats oat oatmeal porridge granola muesli"),
    ("vegetables", "vegetable vegetables veggie vegan vegetarian tofu paneer spinach kale"),
)
CUISINES = (
    ("pasta", {"italian"}), ("curry", {"indian", "thai"}),
    ("rice", {"asian", "japanese", "chinese", "korean"}),
    ("vegetables", {"mediterranean", "mexican", "greek"}),
)


def normalize(value):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", str(value or "")).casefold()))


def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def available_image(key):
    """Return a local key; renderer supplies an accessible panel if all files are missing."""
    if key in IMAGES and (ASSETS / IMAGES[key][0]).is_file():
        return key
    return "recipe"


def recipe_image(recipe):
    """Resolve full recipe records, recommendation records, dictionaries or names.

    Dietary exclusions guard every tier. Identity precedes name keywords, then
    ingredient content, cuisine, dietary category and the generic vegetable meal.
    IDs are deliberately ignored: re-seeding or reordering cannot change imagery.
    """
    name = normalize(recipe if isinstance(recipe, str) else
                     field(recipe, "name") or field(recipe, "recipe_name"))
    tags = normalize(field(recipe, "dietary_tags", ""))
    description = normalize(field(recipe, "description", ""))
    words = set(f"{name} {tags} {description}".split())
    plant = bool(words & {"vegan", "vegetarian", "plant", "meatless", "tofu", "tempeh"})
    ingredients = field(recipe, "ingredients", None) or field(recipe, "requirements", []) or []
    content = " ".join(normalize(field(field(item, "food", {}), "name") or
                                 field(item, "food_name") or
                                 (item if isinstance(item, str) else "")) for item in ingredients)
    chicken = "chicken" in set(f"{name} {content}".split())

    def permitted(key):
        return not (plant and key in {"chicken", "seafood"}) and not (chicken and key == "seafood")

    explicit = EXPLICIT_RECIPES.get(name)
    if explicit and permitted(explicit):
        return available_image(explicit)
    for text in (name, content):
        tokens = set(text.split())
        for key, keywords in KEYWORDS:
            if tokens.intersection(keywords.split()) and permitted(key):
                return available_image(key)
    cuisine = set(normalize(field(recipe, "cuisine", "")).split())
    for key, keywords in CUISINES:
        if cuisine & keywords:
            return available_image(key)
    return available_image("vegetables" if plant else "recipe")


def page_hero_image(page_name):
    return available_image(PAGE_IMAGES.get(normalize(page_name), "recipe"))


def food_category_image(food_or_category):
    """Category serving inspiration, never a claim to depict an exact raw ingredient."""
    return recipe_image(food_or_category)
