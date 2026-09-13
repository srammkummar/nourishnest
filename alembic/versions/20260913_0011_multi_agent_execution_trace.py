"""Persist redacted bounded orchestration audit records."""

import sqlalchemy as sa

from alembic import op

revision = "20260913_0011"
down_revision = "20260913_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider_mode", sa.String(32), nullable=False),
        sa.Column("orchestration_version", sa.String(64), nullable=False),
        sa.Column("interpreted_intent_json", sa.JSON(), nullable=False),
        sa.Column("selected_agents_json", sa.JSON(), nullable=False),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Numeric(18, 6), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("tool_call_count >= 0 AND tool_call_count <= 12", name="ck_agent_run_calls"),
        sa.CheckConstraint("warning_count >= 0 AND duration_ms >= 0", name="ck_agent_run_metrics"),
        sa.CheckConstraint("status IN ('completed','failed','refused','clarification_required')", name="ck_agent_run_status"),
    )
    op.create_index("ix_agent_runs_household_id", "agent_runs", ["household_id"])
    op.create_index("ix_agent_runs_request_id", "agent_runs", ["request_id"])
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_name", sa.String(32), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("tool_name", sa.String(64)),
        sa.Column("input_summary_json", sa.JSON(), nullable=False),
        sa.Column("output_summary_json", sa.JSON(), nullable=False),
        sa.Column("warning_codes_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Numeric(18, 6), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence_number", name="uq_agent_step_sequence"),
        sa.CheckConstraint("sequence_number >= 0 AND duration_ms >= 0", name="ck_agent_step_metrics"),
        sa.CheckConstraint("status IN ('completed','failed','cancelled')", name="ck_agent_step_status"),
    )
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])


def downgrade():
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
