"""Isolated evaluation data. These setup writes never target the application database."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from nourish_nest.models import (
    Allergy,
    Base,
    DietaryPreference,
    Food,
    FoodAllergen,
    FoodDietaryTagRecord,
    Household,
    HouseholdMember,
    PantryItem,
    PantryLocation,
    Recipe,
    RecipeIngredient,
)

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
IDS = {name: UUID(int=index) for index, name in enumerate(
    ("household", "other_household", "adult", "minor", "foreign_member", "vegan_member", "missing"), 1
)}


def evaluation_database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        session.add_all([Household(id=IDS["household"], name="Eval household"),
                         Household(id=IDS["other_household"], name="Private household")])
        session.flush()
        for name, age, owner in (("adult", 35, "household"), ("minor", 15, "household"),
                                 ("foreign_member", 30, "other_household"),
                                 ("vegan_member", 30, "household")):
            person = HouseholdMember(
                id=IDS[name], household_id=IDS[owner], name=name, age=age, sex="female",
                height_cm=165, weight_kg=68, activity_level="moderate", goal="maintain",
                weekly_goal_kg=0, meals_per_day=3,
            )
            if name == "adult":
                person.allergies = [Allergy(allergen="milk", severity="severe")]
            if name == "vegan_member":
                person.dietary_preferences = [DietaryPreference(preference_type="vegan", value="vegan")]
            session.add(person)
        location = PantryLocation(household_id=IDS["household"], name="Cupboard", location_type="pantry")
        session.add(location)
        session.flush()
        for index in range(10):
            vegan = index < 7
            food = Food(
                id=UUID(int=100+index), name=f"Beans {index}" if vegan else f"Milk {index}",
                normalized_name=f"food {index}", source_type="manual",
                serving_quantity=Decimal(1), serving_unit="g", calories_per_serving=Decimal(2),
                protein_g=Decimal("0.1"), carbohydrate_g=Decimal("0.2"), fat_g=Decimal("0.03"),
                fiber_g=Decimal(0), sugar_g=Decimal(0), sodium_mg=Decimal(0),
            )
            food.dietary_tags = [FoodDietaryTagRecord(tag=tag) for tag in (
                ("vegan", "vegetarian", "pescatarian", "halal", "no_beef", "no_pork") if vegan
                else ("vegetarian",)
            )]
            food.allergens = [FoodAllergen(allergen="peanut" if vegan else "milk",
                                          relationship_type="may_contain" if vegan else "contains")]
            recipe = Recipe(
                id=UUID(int=200+index), name=f"Beans dinner {index}" if vegan else f"Dairy dinner {index}",
                household_id=None if index == 6 else IDS["household"], servings=Decimal(2),
                cooking_minutes=10+index, preparation_minutes=5,
            )
            recipe.ingredients = [RecipeIngredient(food=food, quantity=Decimal(100),
                                                    unit="pinch" if index == 9 else "g",
                                                    display_order=0)]
            session.add(recipe)
            session.add(PantryItem(household_id=IDS["household"], location_id=location.id,
                                   food=food, quantity=Decimal(30), unit="g", status="active",
                                   expiration_date=NOW.date()+timedelta(days=1)))
        private = Recipe(id=UUID(int=999), household_id=IDS["other_household"],
                         name="Private recipe", servings=Decimal(1))
        session.add(private)
        session.commit()
    return engine, sessions
