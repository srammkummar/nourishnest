"""Create pantry inventory Phase 4 tables.

Revision ID: 20260908_0004
Revises: 20260907_0003
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0004"
down_revision: str | None = "20260907_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pantry_locations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("location_type", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("household_id", "name", name="uq_pantry_location_name"),
    )
    op.create_index("ix_pantry_locations_household_id", "pantry_locations", ["household_id"])
    op.create_table(
        "pantry_items",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("location_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("food_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("canonical_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("canonical_unit", sa.String(length=16), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("opened_date", sa.Date(), nullable=True),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("best_before_date", sa.Date(), nullable=True),
        sa.Column("lot_note", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["food_id"], ["foods.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["pantry_locations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pantry_items_household_id", "pantry_items", ["household_id"])
    op.create_index("ix_pantry_items_location_id", "pantry_items", ["location_id"])
    op.create_index("ix_pantry_items_food_id", "pantry_items", ["food_id"])
    op.create_index("ix_pantry_items_status", "pantry_items", ["status"])
    op.create_index("ix_pantry_items_expiration_date", "pantry_items", ["expiration_date"])
    op.create_table(
        "pantry_stock_rules",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("food_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("threshold_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("threshold_unit", sa.String(length=32), nullable=False),
        sa.Column("preferred_reorder_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("preferred_reorder_unit", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["food_id"], ["foods.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("household_id", "food_id", name="uq_pantry_stock_rule_food"),
    )
    op.create_index("ix_pantry_stock_rules_household_id", "pantry_stock_rules", ["household_id"])
    op.create_index("ix_pantry_stock_rules_food_id", "pantry_stock_rules", ["food_id"])
    op.create_table(
        "pantry_transactions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("pantry_item_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("transaction_type", sa.String(length=16), nullable=False),
        sa.Column("quantity_change", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("canonical_quantity_change", sa.Numeric(18, 6), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pantry_item_id"], ["pantry_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "household_id",
            "transaction_type",
            "idempotency_key",
            name="uq_pantry_transaction_idempotency",
        ),
    )
    op.create_index("ix_pantry_transactions_pantry_item_id", "pantry_transactions", ["pantry_item_id"])


def downgrade() -> None:
    op.drop_index("ix_pantry_transactions_pantry_item_id", table_name="pantry_transactions")
    op.drop_table("pantry_transactions")
    op.drop_index("ix_pantry_stock_rules_food_id", table_name="pantry_stock_rules")
    op.drop_index("ix_pantry_stock_rules_household_id", table_name="pantry_stock_rules")
    op.drop_table("pantry_stock_rules")
    op.drop_index("ix_pantry_items_expiration_date", table_name="pantry_items")
    op.drop_index("ix_pantry_items_status", table_name="pantry_items")
    op.drop_index("ix_pantry_items_food_id", table_name="pantry_items")
    op.drop_index("ix_pantry_items_location_id", table_name="pantry_items")
    op.drop_index("ix_pantry_items_household_id", table_name="pantry_items")
    op.drop_table("pantry_items")
    op.drop_index("ix_pantry_locations_household_id", table_name="pantry_locations")
    op.drop_table("pantry_locations")