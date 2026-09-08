"""Create household grocery list foundation.

Revision ID: 20260908_0005
Revises: 20260908_0004
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0005"
down_revision: str | None = "20260908_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grocery_lists",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("household_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.Enum("draft", "active", "completed", "archived",
                  native_enum=False, create_constraint=True, name="grocery_list_status", length=16),
                  nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("version >= 1", name="ck_grocery_lists_version"),
    )
    op.create_index("ix_grocery_lists_household_status", "grocery_lists", ["household_id", "status"])
    op.create_index("ix_grocery_lists_status", "grocery_lists", ["status"])
    op.create_table(
        "grocery_list_items",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("grocery_list_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("grocery_lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("food_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("foods.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("required_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("required_unit", sa.String(32), nullable=False),
        sa.Column("purchased_quantity", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("source_type", sa.Enum("manual", "recipe", "low_stock", native_enum=False,
                  create_constraint=True, name="grocery_item_source_type", length=16),
                  nullable=False, server_default="manual"),
        sa.Column("source_reference_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("checked", sa.Boolean(create_constraint=True, name="grocery_item_checked"),
                  nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("required_quantity >= 0", name="ck_grocery_items_required_quantity"),
        sa.CheckConstraint("purchased_quantity >= 0", name="ck_grocery_items_purchased_quantity"),
        sa.CheckConstraint("version >= 1", name="ck_grocery_items_version"),
    )
    op.create_index("ix_grocery_list_items_list_checked", "grocery_list_items", ["grocery_list_id", "checked"])
    op.create_index("ix_grocery_list_items_food_id", "grocery_list_items", ["food_id"])


def downgrade() -> None:
    op.drop_table("grocery_list_items")
    op.drop_table("grocery_lists")
