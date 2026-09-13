from types import SimpleNamespace

import pytest

from nourish_nest import ui_assets, ui_design


@pytest.mark.parametrize(("name", "expected"), [
    ("CHICKEN bowl", "chicken"), ("Grilled fish", "seafood"),
    ("Vegetable biryani", "rice"), ("Tomato pasta", "pasta"),
    ("Vegetable soup", "soup"), ("Summer salad", "salad"),
    ("Overnight OATS", "breakfast"), ("Lentil dal", "legumes"),
    ("Thai curry", "curry"), ("Vegetable Khichdi", "rice"),
    ("Dal Tadka", "legumes"), ("Chana Masala", "legumes"),
    ("Palak Paneer", "curry"), ("Chicken Rice Bowl", "chicken"),
])
def test_relevant_categories(name, expected):
    assert ui_assets.recipe_image({"name": name, "cuisine": "Indian-inspired"}) == expected


@pytest.mark.parametrize("tag", ["VEGAN", "Vegetarian", "plant-based", "meatless", "tofu"])
@pytest.mark.parametrize("name", ["Chicken Rice Bowl", "Salmon", "Fish curry", "Chicken soup"])
def test_dietary_safety_overrides_all_matches(tag, name):
    assert ui_assets.recipe_image({"name": name, "dietary_tags": [tag]}) not in {"chicken", "seafood"}
    assert ui_assets.recipe_image(f"{tag} {name}") not in {"chicken", "seafood"}


def test_identity_normalization_content_and_precedence():
    assert ui_assets.recipe_image("  CHICKEN--RICE_bowl ") == "chicken"
    assert ui_assets.recipe_image("Chicken and seafood") == "chicken"
    assert ui_assets.recipe_image({"name": "Special", "cuisine": "ITALIAN"}) == "pasta"
    assert ui_assets.recipe_image({"name": "Soup", "cuisine": "Italian"}) == "soup"
    assert ui_assets.recipe_image({"name": "Special", "ingredients": [{"food": {"name": "Chicken"}}]}) == "chicken"
    assert ui_assets.recipe_image(SimpleNamespace(recipe_name="Special", requirements=[SimpleNamespace(food_name="Fish")])) == "seafood"
    assert ui_assets.food_category_image({"name": "Basmati rice, cooked"}) == "rice"
    assert ui_assets.food_category_image("Dal, cooked") == "legumes"


def test_unknown_and_missing_asset_fallback(monkeypatch, tmp_path):
    assert ui_assets.recipe_image(None) == "recipe"
    assert ui_assets.recipe_image({"name": "Mystery supper"}) == "recipe"
    monkeypatch.setattr(ui_assets, "ASSETS", tmp_path)
    assert ui_assets.recipe_image("Chicken") == "recipe"
    monkeypatch.setattr(ui_design, "ASSETS", tmp_path)
    markup = ui_design.image_html(ui_assets.recipe_image("Chicken"))
    assert '<img' not in markup and 'role="img"' in markup


def test_category_assets_are_distinct_and_deterministic():
    names = ["Chicken", "Fish", "Rice", "Pasta", "Soup", "Salad", "Oats", "Dal", "Curry"]
    paths = [ui_assets.IMAGES[ui_assets.recipe_image(n)][0] for n in names]
    assert len(set(paths)) == len(names)
    for name in reversed(names):
        first = ui_assets.recipe_image({"name": name, "id": "first"})
        assert first == ui_assets.recipe_image({"name": name, "id": "different"})


@pytest.mark.parametrize(("page", "key"), [
    ("Dashboard", "vegetables"), ("Recipes", "pasta"), ("Meal Planner", "meal-prep"),
    ("Pantry", "pantry"), ("Grocery Lists", "grocery"), ("Nutrition", "nutrition"),
    ("Household", "kitchen"), ("AI Assistant", "assistant"),
])
def test_relevant_page_heroes(page, key):
    assert ui_assets.page_hero_image(page.upper()) == key
    assert ui_design.HEADERS[page][1] == key
    assert len(set(ui_assets.PAGE_IMAGES.values())) == 8


@pytest.mark.parametrize("key", ui_assets.IMAGES)
def test_registry_is_local_accessible_and_present(key):
    relative, alt = ui_assets.IMAGES[key]
    assert (ui_assets.ASSETS / relative).is_file()
    assert not relative.startswith(("http://", "https://"))
    assert alt.strip()
    markup = ui_design.image_html(key)
    assert 'alt="' in markup
    assert "http://" not in markup and "https://" not in markup
