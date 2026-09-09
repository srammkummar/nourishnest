"""Version recipes and persist household recipe creation idempotency."""

import sqlalchemy as sa

from alembic import op

revision = "20260909_0009"
down_revision = "20260909_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recipes", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        "recipe_creation_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "household_id",
            sa.Uuid(),
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "recipe_id", sa.Uuid(), sa.ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "household_id", "idempotency_key", name="uq_recipe_creation_household_key"
        ),
    )
    op.create_index(
        "ix_recipe_creation_records_household_id", "recipe_creation_records", ["household_id"]
    )
    op.create_index(
        "ix_recipe_creation_records_recipe_id", "recipe_creation_records", ["recipe_id"]
    )


def downgrade() -> None:
    op.drop_table("recipe_creation_records")
    op.drop_column("recipes", "version")
