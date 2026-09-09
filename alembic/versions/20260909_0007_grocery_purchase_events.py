"""Add durable grocery purchase events, including purchases without pantry intake.

Revision ID: 20260909_0007
Revises: 20260908_0006
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260909_0007"
down_revision: str | None = "20260908_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grocery_purchase_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("household_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grocery_list_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("grocery_lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grocery_list_item_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("grocery_list_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("purchased_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("purchased_unit", sa.String(32), nullable=False),
        sa.Column("item_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("item_unit", sa.String(32), nullable=False),
        sa.Column("purchased_total", sa.Numeric(18, 6), nullable=False),
        sa.Column("checked", sa.Boolean(create_constraint=True, name="grocery_purchase_checked"), nullable=False),
        sa.Column("item_version", sa.Integer(), nullable=False),
        sa.Column("grocery_list_version", sa.Integer(), nullable=False),
        sa.Column("grocery_list_status", sa.Enum("draft", "active", "completed", "archived", native_enum=False,
                  create_constraint=True, name="grocery_purchase_list_status", length=16), nullable=False),
        sa.Column("add_to_pantry", sa.Boolean(create_constraint=True, name="grocery_purchase_intake"), nullable=False),
        sa.Column("allow_overpurchase", sa.Boolean(create_constraint=True, name="grocery_purchase_overpurchase"), nullable=False),
        sa.Column("pantry_location_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("pantry_locations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("purchase_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("pantry_item_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("pantry_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pantry_transaction_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("pantry_transactions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("household_id", "grocery_list_id", "grocery_list_item_id", "idempotency_key",
                            name="uq_grocery_purchase_key"),
        sa.CheckConstraint("purchased_quantity > 0", name="ck_grocery_purchase_quantity"),
        sa.CheckConstraint("item_quantity > 0", name="ck_grocery_purchase_item_quantity"),
        sa.CheckConstraint("purchased_total >= item_quantity", name="ck_grocery_purchase_total"),
        sa.CheckConstraint("purchase_price >= 0", name="ck_grocery_purchase_price"),
    )
    for column in ("household_id", "grocery_list_id", "grocery_list_item_id", "pantry_item_id", "created_at"):
        op.create_index(f"ix_grocery_purchase_events_{column}", "grocery_purchase_events", [column])


def downgrade() -> None:
    op.drop_table("grocery_purchase_events")
