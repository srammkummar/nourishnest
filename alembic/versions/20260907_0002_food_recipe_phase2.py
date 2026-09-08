"""Create Food and Recipe Database Phase 2 tables.

Revision ID: 20260907_0002
Revises: 20260907_0001
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260907_0002"
down_revision: str | None = "20260907_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "foods",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("brand", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("external_source_identifier", sa.String(length=200), nullable=True),
        sa.Column("serving_quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("serving_unit", sa.String(length=32), nullable=False),
        sa.Column("grams_per_serving", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("calories_per_serving", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("protein_g", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("carbohydrate_g", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("fat_g", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("fiber_g", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("sugar_g", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("sodium_mg", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_foods_normalized_name", "foods", ["normalized_name"])

    op.create_table(
        "food_allergens",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("food_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("allergen", sa.String(length=100), nullable=False),
        sa.Column("relationship_type", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["food_id"], ["foods.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_food_allergens_food_id", "food_allergens", ["food_id"])

    op.create_table(
        "food_dietary_tags",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("food_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["food_id"], ["foods.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_food_dietary_tags_food_id", "food_dietary_tags", ["food_id"])

    op.create_table(
        "recipes",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cuisine", sa.String(length=100), nullable=True),
        sa.Column("preparation_minutes", sa.Integer(), nullable=False),
        sa.Column("cooking_minutes", sa.Integer(), nullable=False),
        sa.Column("servings", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recipes_household_id", "recipes", ["household_id"])

    op.create_table(
        "recipe_ingredients",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("recipe_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("food_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("preparation_note", sa.String(length=300), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["food_id"], ["foods.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recipe_ingredients_recipe_id", "recipe_ingredients", ["recipe_id"])
    op.create_index("ix_recipe_ingredients_food_id", "recipe_ingredients", ["food_id"])

    op.create_table(
        "recipe_instructions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("recipe_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recipe_instructions_recipe_id", "recipe_instructions", ["recipe_id"])


def downgrade() -> None:
    op.drop_index("ix_recipe_instructions_recipe_id", table_name="recipe_instructions")
    op.drop_table("recipe_instructions")
    op.drop_index("ix_recipe_ingredients_food_id", table_name="recipe_ingredients")
    op.drop_index("ix_recipe_ingredients_recipe_id", table_name="recipe_ingredients")
    op.drop_table("recipe_ingredients")
    op.drop_index("ix_recipes_household_id", table_name="recipes")
    op.drop_table("recipes")
    op.drop_index("ix_food_dietary_tags_food_id", table_name="food_dietary_tags")
    op.drop_table("food_dietary_tags")
    op.drop_index("ix_food_allergens_food_id", table_name="food_allergens")
    op.drop_table("food_allergens")
    op.drop_index("ix_foods_normalized_name", table_name="foods")
    op.drop_table("foods")