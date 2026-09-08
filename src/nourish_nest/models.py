import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nourish_nest.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class PreferenceType(StrEnum):
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    PESCATARIAN = "pescatarian"
    HALAL = "halal"
    NO_BEEF = "no-beef"
    NO_PORK = "no-pork"
    CUSTOM = "custom"


class AllergySeverity(StrEnum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class FoodSourceType(StrEnum):
    DEVELOPMENT_FIXTURE = "development_fixture"
    MANUAL = "manual"
    EXTERNAL = "external"


class FoodAllergenRelationship(StrEnum):
    CONTAINS = "contains"
    MAY_CONTAIN = "may_contain"
    FREE_FROM = "free_from"


class FoodDietaryTag(StrEnum):
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    PESCATARIAN = "pescatarian"
    HALAL = "halal"
    NO_BEEF = "no_beef"
    NO_PORK = "no_pork"


class Household(Base):
    __tablename__ = "households"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    members: Mapped[list["HouseholdMember"]] = relationship(
        back_populates="household", cascade="all, delete-orphan", passive_deletes=True
    )
    recipes: Mapped[list["Recipe"]] = relationship(
        back_populates="household", cascade="all, delete-orphan", passive_deletes=True
    )


class HouseholdMember(Base):
    __tablename__ = "household_members"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    sex: Mapped[str] = mapped_column(String(16), nullable=False)
    height_cm: Mapped[float] = mapped_column(Float, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    activity_level: Mapped[str] = mapped_column(String(32), nullable=False)
    goal: Mapped[str] = mapped_column(String(16), nullable=False)
    weekly_goal_kg: Mapped[float] = mapped_column(Float, nullable=False, default=0.25)
    meals_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    household: Mapped[Household] = relationship(back_populates="members")
    dietary_preferences: Mapped[list["DietaryPreference"]] = relationship(
        back_populates="member", cascade="all, delete-orphan", passive_deletes=True
    )
    allergies: Mapped[list["Allergy"]] = relationship(
        back_populates="member", cascade="all, delete-orphan", passive_deletes=True
    )


class DietaryPreference(Base):
    __tablename__ = "dietary_preferences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("household_members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    preference_type: Mapped[PreferenceType] = mapped_column(
        Enum(PreferenceType, native_enum=False, length=32), nullable=False
    )
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    member: Mapped[HouseholdMember] = relationship(back_populates="dietary_preferences")


class Allergy(Base):
    __tablename__ = "allergies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("household_members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    allergen: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[AllergySeverity] = mapped_column(
        Enum(AllergySeverity, native_enum=False, length=16), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    member: Mapped[HouseholdMember] = relationship(back_populates="allergies")


class Food(Base):
    __tablename__ = "foods"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[FoodSourceType] = mapped_column(
        Enum(FoodSourceType, native_enum=False, length=32), nullable=False
    )
    external_source_identifier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    serving_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    serving_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    grams_per_serving: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    calories_per_serving: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    carbohydrate_g: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    fiber_g: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    sugar_g: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    sodium_mg: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    allergens: Mapped[list["FoodAllergen"]] = relationship(
        back_populates="food", cascade="all, delete-orphan", passive_deletes=True
    )
    dietary_tags: Mapped[list["FoodDietaryTagRecord"]] = relationship(
        back_populates="food", cascade="all, delete-orphan", passive_deletes=True
    )
    recipe_ingredients: Mapped[list["RecipeIngredient"]] = relationship(back_populates="food")


class FoodAllergen(Base):
    __tablename__ = "food_allergens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    food_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("foods.id", ondelete="CASCADE"), nullable=False, index=True
    )
    allergen: Mapped[str] = mapped_column(String(100), nullable=False)
    relationship_type: Mapped[FoodAllergenRelationship] = mapped_column(
        Enum(FoodAllergenRelationship, native_enum=False, length=16), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    food: Mapped[Food] = relationship(back_populates="allergens")


class FoodDietaryTagRecord(Base):
    __tablename__ = "food_dietary_tags"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    food_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("foods.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tag: Mapped[FoodDietaryTag] = mapped_column(
        Enum(FoodDietaryTag, native_enum=False, length=16), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    food: Mapped[Food] = relationship(back_populates="dietary_tags")


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cuisine: Mapped[str | None] = mapped_column(String(100), nullable=True)
    preparation_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cooking_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    servings: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    household: Mapped[Household | None] = relationship(back_populates="recipes")
    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", passive_deletes=True, order_by="RecipeIngredient.display_order"
    )
    instructions: Mapped[list["RecipeInstruction"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", passive_deletes=True, order_by="RecipeInstruction.step_number"
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    food_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("foods.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    preparation_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")
    food: Mapped[Food] = relationship(back_populates="recipe_ingredients")


class RecipeInstruction(Base):
    __tablename__ = "recipe_instructions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    recipe: Mapped[Recipe] = relationship(back_populates="instructions")