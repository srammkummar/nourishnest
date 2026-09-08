"""Create Database Phase 1 tables.

Revision ID: 20260907_0001
Revises:
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260907_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "households",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "household_members",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("household_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("sex", sa.String(length=16), nullable=False),
        sa.Column("height_cm", sa.Float(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("activity_level", sa.String(length=32), nullable=False),
        sa.Column("goal", sa.String(length=16), nullable=False),
        sa.Column("weekly_goal_kg", sa.Float(), nullable=False),
        sa.Column("meals_per_day", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_household_members_household_id", "household_members", ["household_id"])
    op.create_table(
        "dietary_preferences",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("member_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("preference_type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["household_members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dietary_preferences_member_id", "dietary_preferences", ["member_id"])
    op.create_table(
        "allergies",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("member_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("allergen", sa.String(length=200), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["household_members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_allergies_member_id", "allergies", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_allergies_member_id", table_name="allergies")
    op.drop_table("allergies")
    op.drop_index("ix_dietary_preferences_member_id", table_name="dietary_preferences")
    op.drop_table("dietary_preferences")
    op.drop_index("ix_household_members_household_id", table_name="household_members")
    op.drop_table("household_members")
    op.drop_table("households")