"""Persist scoped, versioned knowledge documents and immutable chunks."""

import sqlalchemy as sa

from alembic import op

revision = "20260913_0010"
down_revision = "20260909_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source_name", sa.String(300), nullable=False),
        sa.Column("source_uri", sa.String(2000)),
        sa.Column("source_type", sa.Enum(
            "nourishnest_documentation", "nutrition_guidance", "food_safety_guidance",
            "recipe_document", "user_document", native_enum=False, create_constraint=True,
            name="knowledge_source_type"), nullable=False),
        sa.Column("visibility", sa.Enum("global", "household", native_enum=False,
                  create_constraint=True, name="knowledge_visibility"), nullable=False),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE")),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("scoped_hash", sa.String(64), nullable=False),
        sa.Column("lineage_key", sa.String(64), nullable=False),
        sa.Column("status", sa.Enum("active", "superseded", "failed", native_enum=False,
                  create_constraint=True, name="knowledge_status"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("(visibility = 'global' AND household_id IS NULL) OR "
                           "(visibility = 'household' AND household_id IS NOT NULL)",
                           name="ck_knowledge_ownership"),
        sa.CheckConstraint("version >= 1 AND lock_version >= 1", name="ck_knowledge_versions"),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_knowledge_hash"),
        sa.CheckConstraint("length(trim(title)) > 0 AND length(trim(source_name)) > 0",
                           name="ck_knowledge_labels"),
        sa.UniqueConstraint("lineage_key", "version", name="uq_knowledge_revision"),
    )
    for name, column in (("lineage", "lineage_key"), ("hash", "scoped_hash")):
        op.create_index(f"uq_knowledge_active_{name}", "knowledge_documents", [column],
                        unique=True, sqlite_where=sa.text("status = 'active'"),
                        postgresql_where=sa.text("status = 'active'"))
    op.create_index("ix_knowledge_scope_source", "knowledge_documents",
                    ["household_id", "status", "source_type"])
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey(
            "knowledge_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.Text()),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunk_index"),
        sa.CheckConstraint("chunk_index >= 0 AND word_count > 0", name="ck_knowledge_chunk_counts"),
        sa.CheckConstraint("length(trim(content)) > 0 AND length(content_hash) = 64",
                           name="ck_knowledge_chunk_content"),
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
