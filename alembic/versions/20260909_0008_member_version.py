"""Add optimistic concurrency to household members."""

import sqlalchemy as sa

from alembic import op

revision = "20260909_0008"
down_revision = "20260909_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "household_members", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )


def downgrade() -> None:
    op.drop_column("household_members", "version")
