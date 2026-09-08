"""Add USDA provenance fields to foods.

Revision ID: 20260907_0003
Revises: 20260907_0002
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260907_0003"
down_revision: str | None = "20260907_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("foods", sa.Column("source_data_type", sa.String(length=64), nullable=True))
    op.add_column("foods", sa.Column("source_attribution", sa.String(length=300), nullable=True))
    op.add_column("foods", sa.Column("source_retrieved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("foods", sa.Column("source_provider", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_food_source_identifier",
        "foods",
        ["source_provider", "external_source_identifier"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_food_source_identifier", table_name="foods")
    op.drop_column("foods", "source_provider")
    op.drop_column("foods", "source_retrieved_at")
    op.drop_column("foods", "source_attribution")
    op.drop_column("foods", "source_data_type")