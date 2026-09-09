import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
)
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


class PantryLocationType(StrEnum):
    PANTRY = "pantry"
    REFRIGERATOR = "refrigerator"
    FREEZER = "freezer"
    CABINET = "cabinet"
    CUSTOM = "custom"


class PantryItemStatus(StrEnum):
    ACTIVE = "active"
    DEPLETED = "depleted"
    EXPIRED = "expired"
    DISCARDED = "discarded"


class PantryTransactionType(StrEnum):
    RESTOCK = "restock"
    CONSUME = "consume"
    ADJUST = "adjust"
    TRANSFER = "transfer"
    DISCARD = "discard"
    EXPIRE = "expire"


class GroceryListStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class GroceryItemSourceType(StrEnum):
    MANUAL = "manual"
    RECIPE = "recipe"
    LOW_STOCK = "low_stock"


class GroceryList(Base):
    __tablename__ = "grocery_lists"
    __table_args__ = (
        Index("ix_grocery_lists_household_status", "household_id", "status"),
        CheckConstraint("version >= 1", name="ck_grocery_lists_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[GroceryListStatus] = mapped_column(
        Enum(GroceryListStatus, values_callable=lambda cls: [e.value for e in cls],
             native_enum=False, create_constraint=True, name="grocery_list_status", length=16),
        nullable=False, default=GroceryListStatus.DRAFT, server_default="draft", index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    household: Mapped["Household"] = relationship(back_populates="grocery_lists")
    items: Mapped[list["GroceryListItem"]] = relationship(
        back_populates="grocery_list", cascade="all, delete-orphan", passive_deletes=True
    )
    generation_runs: Mapped[list["GroceryGenerationRun"]] = relationship(
        back_populates="grocery_list", cascade="all, delete-orphan", passive_deletes=True
    )
    __mapper_args__: ClassVar[dict[str, object]] = {"version_id_col": version}


class GroceryListItem(Base):
    __tablename__ = "grocery_list_items"
    __table_args__ = (
        Index("ix_grocery_list_items_list_checked", "grocery_list_id", "checked"),
        CheckConstraint("required_quantity >= 0", name="ck_grocery_items_required_quantity"),
        CheckConstraint("purchased_quantity >= 0", name="ck_grocery_items_purchased_quantity"),
        CheckConstraint("version >= 1", name="ck_grocery_items_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grocery_list_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("grocery_lists.id", ondelete="CASCADE"), nullable=False
    )
    food_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("foods.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    required_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    required_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    purchased_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, default=Decimal(0), server_default="0"
    )
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_type: Mapped[GroceryItemSourceType] = mapped_column(
        Enum(GroceryItemSourceType, values_callable=lambda cls: [e.value for e in cls],
             native_enum=False, create_constraint=True, name="grocery_item_source_type", length=16),
        nullable=False, default=GroceryItemSourceType.MANUAL, server_default="manual",
    )
    source_reference_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    generation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("grocery_generation_runs.id", name="fk_grocery_item_generation_run", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    checked: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="grocery_item_checked"),
        nullable=False, default=False, server_default=false(),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    grocery_list: Mapped[GroceryList] = relationship(back_populates="items")
    food: Mapped["Food | None"] = relationship()
    generation_run: Mapped["GroceryGenerationRun | None"] = relationship(back_populates="items")
    recipe_sources: Mapped[list["GroceryItemRecipeSource"]] = relationship(
        back_populates="grocery_list_item", cascade="all, delete-orphan", passive_deletes=True
    )
    purchases: Mapped[list["GroceryPurchaseEvent"]] = relationship(
        back_populates="grocery_list_item", cascade="all, delete-orphan", passive_deletes=True
    )
    __mapper_args__: ClassVar[dict[str, object]] = {"version_id_col": version}


class GroceryPurchaseEvent(Base):
    __tablename__ = "grocery_purchase_events"
    __table_args__ = (
        UniqueConstraint("household_id", "grocery_list_id", "grocery_list_item_id", "idempotency_key",
                         name="uq_grocery_purchase_key"),
        CheckConstraint("purchased_quantity > 0", name="ck_grocery_purchase_quantity"),
        CheckConstraint("item_quantity > 0", name="ck_grocery_purchase_item_quantity"),
        CheckConstraint("purchased_total >= item_quantity", name="ck_grocery_purchase_total"),
        CheckConstraint("purchase_price >= 0", name="ck_grocery_purchase_price"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grocery_list_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("grocery_lists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grocery_list_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("grocery_list_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purchased_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    purchased_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    item_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    item_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    purchased_total: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    checked: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="grocery_purchase_checked"), nullable=False)
    item_version: Mapped[int] = mapped_column(Integer, nullable=False)
    grocery_list_version: Mapped[int] = mapped_column(Integer, nullable=False)
    grocery_list_status: Mapped[GroceryListStatus] = mapped_column(
        Enum(GroceryListStatus, values_callable=lambda cls: [e.value for e in cls], native_enum=False,
             create_constraint=True, name="grocery_purchase_list_status", length=16), nullable=False
    )
    add_to_pantry: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="grocery_purchase_intake"), nullable=False
    )
    allow_overpurchase: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="grocery_purchase_overpurchase"), nullable=False
    )
    pantry_location_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pantry_locations.id", ondelete="SET NULL"), nullable=True
    )
    expiration_date: Mapped[date | None] = mapped_column(nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    pantry_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pantry_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pantry_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pantry_transactions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    grocery_list_item: Mapped[GroceryListItem] = relationship(back_populates="purchases")
    pantry_item: Mapped["PantryItem | None"] = relationship()
    pantry_transaction: Mapped["PantryTransaction | None"] = relationship()


class GroceryGenerationRun(Base):
    __tablename__ = "grocery_generation_runs"
    __table_args__ = (
        UniqueConstraint("household_id", "grocery_list_id", "idempotency_key",
                         name="uq_grocery_generation_run_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grocery_list_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("grocery_lists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    household: Mapped["Household"] = relationship()
    grocery_list: Mapped[GroceryList] = relationship(back_populates="generation_runs")
    items: Mapped[list[GroceryListItem]] = relationship(
        back_populates="generation_run", passive_deletes="all"
    )


class GroceryItemRecipeSource(Base):
    __tablename__ = "grocery_item_recipe_sources"
    __table_args__ = (
        CheckConstraint("required_quantity >= 0", name="ck_grocery_recipe_source_quantity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grocery_list_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("grocery_list_items.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("recipes.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"),
        nullable=False, index=True,
    )
    recipe_ingredient_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recipe_ingredients.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    required_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    canonical_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    grocery_list_item: Mapped[GroceryListItem] = relationship(back_populates="recipe_sources")
    recipe: Mapped["Recipe"] = relationship()
    recipe_ingredient: Mapped["RecipeIngredient | None"] = relationship()


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
    pantry_locations: Mapped[list["PantryLocation"]] = relationship(
        back_populates="household", cascade="all, delete-orphan", passive_deletes=True
    )
    pantry_items: Mapped[list["PantryItem"]] = relationship(
        back_populates="household", cascade="all, delete-orphan", passive_deletes=True
    )
    pantry_stock_rules: Mapped[list["PantryStockRule"]] = relationship(
        back_populates="household", cascade="all, delete-orphan", passive_deletes=True
    )
    grocery_lists: Mapped[list["GroceryList"]] = relationship(
        back_populates="household", cascade="all, delete-orphan", passive_deletes=True
    )


class HouseholdMember(Base):
    __tablename__ = "household_members"

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    __mapper_args__: ClassVar[dict[str, object]] = {"version_id_col": version}

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
    __table_args__ = (
        UniqueConstraint("source_provider", "external_source_identifier", name="uq_food_source_identifier"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[FoodSourceType] = mapped_column(
        Enum(FoodSourceType, native_enum=False, length=32), nullable=False
    )
    external_source_identifier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_data_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_attribution: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    pantry_items: Mapped[list["PantryItem"]] = relationship(back_populates="food")


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

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    __mapper_args__: ClassVar[dict[str, object]] = {"version_id_col": version}

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


class RecipeCreationRecord(Base):
    __tablename__ = "recipe_creation_records"
    __table_args__ = (
        UniqueConstraint("household_id", "idempotency_key", name="uq_recipe_creation_household_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Keep a tombstone after deletion; an old key must never create another recipe.
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


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


class PantryLocation(Base):
    __tablename__ = "pantry_locations"
    __table_args__ = (UniqueConstraint("household_id", "name", name="uq_pantry_location_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    location_type: Mapped[PantryLocationType] = mapped_column(
        Enum(PantryLocationType, native_enum=False, length=32), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    household: Mapped[Household] = relationship(back_populates="pantry_locations")
    items: Mapped[list["PantryItem"]] = relationship(back_populates="location")


class PantryItem(Base):
    __tablename__ = "pantry_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pantry_locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    food_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("foods.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    canonical_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    purchase_date: Mapped[date | None] = mapped_column(nullable=True)
    opened_date: Mapped[date | None] = mapped_column(nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(nullable=True, index=True)
    best_before_date: Mapped[date | None] = mapped_column(nullable=True)
    lot_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[PantryItemStatus] = mapped_column(
        Enum(PantryItemStatus, native_enum=False, length=16), nullable=False, default=PantryItemStatus.ACTIVE, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    household: Mapped[Household] = relationship(back_populates="pantry_items")
    location: Mapped[PantryLocation] = relationship(back_populates="items")
    food: Mapped[Food] = relationship(back_populates="pantry_items")
    transactions: Mapped[list["PantryTransaction"]] = relationship(
        back_populates="pantry_item", cascade="all, delete-orphan", passive_deletes=True
    )
    __mapper_args__: ClassVar[dict[str, object]] = {"version_id_col": version}


class PantryStockRule(Base):
    __tablename__ = "pantry_stock_rules"
    __table_args__ = (UniqueConstraint("household_id", "food_id", name="uq_pantry_stock_rule_food"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    food_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("foods.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    threshold_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    threshold_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    preferred_reorder_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    preferred_reorder_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    household: Mapped[Household] = relationship(back_populates="pantry_stock_rules")
    food: Mapped[Food] = relationship()


class PantryTransaction(Base):
    __tablename__ = "pantry_transactions"
    __table_args__ = (
        UniqueConstraint(
            "household_id",
            "transaction_type",
            "idempotency_key",
            name="uq_pantry_transaction_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pantry_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pantry_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transaction_type: Mapped[PantryTransactionType] = mapped_column(
        Enum(PantryTransactionType, native_enum=False, length=16), nullable=False
    )
    quantity_change: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_quantity_change: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    pantry_item: Mapped[PantryItem] = relationship(back_populates="transactions")
