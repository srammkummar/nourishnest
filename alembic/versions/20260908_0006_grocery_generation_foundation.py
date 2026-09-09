"""Add grocery generation persistence foundation.

Revision ID: 20260908_0006
Revises: 20260908_0005
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0006"
down_revision: str | None = "20260908_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grocery_generation_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("household_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grocery_list_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("grocery_lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("calculation_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("household_id", "grocery_list_id", "idempotency_key",
                            name="uq_grocery_generation_run_key"),
    )
    for column in ("household_id", "grocery_list_id", "created_at"):
        op.create_index(f"ix_grocery_generation_runs_{column}", "grocery_generation_runs", [column])
    with op.batch_alter_table("grocery_list_items") as batch:
        batch.add_column(sa.Column("generation_run_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_foreign_key("fk_grocery_item_generation_run", "grocery_generation_runs",
                                 ["generation_run_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_grocery_list_items_generation_run_id", ["generation_run_id"])
    op.create_table(
        "grocery_item_recipe_sources",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("grocery_list_item_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("grocery_list_items.id", ondelete="CASCADE"), nullable=False),
        # Deferred restriction allows household cascades to remove recipes and sources together.
        sa.Column("recipe_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("recipes.id", ondelete="NO ACTION", deferrable=True,
                                initially="DEFERRED"), nullable=False),
        sa.Column("recipe_ingredient_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("recipe_ingredients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("required_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("canonical_unit", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("required_quantity >= 0", name="ck_grocery_recipe_source_quantity"),
    )
    for column in ("grocery_list_item_id", "recipe_id", "recipe_ingredient_id"):
        op.create_index(f"ix_grocery_item_recipe_sources_{column}", "grocery_item_recipe_sources", [column])


def downgrade() -> None:
    op.drop_table("grocery_item_recipe_sources")
    with op.batch_alter_table("grocery_list_items") as batch:
        batch.drop_index("ix_grocery_list_items_generation_run_id")
        batch.drop_constraint("fk_grocery_item_generation_run", type_="foreignkey")
        batch.drop_column("generation_run_id")
    op.drop_table("grocery_generation_runs")
