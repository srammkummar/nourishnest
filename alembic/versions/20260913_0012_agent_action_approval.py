"""Immutable review evidence, human decisions and controlled execution audit."""

import sqlalchemy as sa

from alembic import op

revision = "20260913_0012"
down_revision = "20260913_0011"
branch_labels = None
depends_on = None


def home():
    return sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False)


def proposal():
    return sa.Column("proposal_id", sa.Uuid(), sa.ForeignKey("agent_action_proposals.id", ondelete="CASCADE"), nullable=False)


def upgrade():
    op.create_table("agent_run_snapshots",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True), home(),
        sa.Column("snapshot_json", sa.JSON(), nullable=False), sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_agent_run_snapshots_household_id", "agent_run_snapshots", ["household_id"])
    op.create_table("agent_action_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True), home(),
        sa.Column("agent_run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("canonical_payload_json", sa.JSON(), nullable=False), sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("critic_result_json", sa.JSON(), nullable=False), sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *[sa.Column(name, sa.DateTime(timezone=True)) for name in (
            "approved_at", "rejected_at", "cancelled_at", "execution_started_at", "execution_completed_at")],
        sa.Column("result_reference_id", sa.Uuid()), sa.Column("failure_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action_type = 'create_grocery_list'", name="ck_proposal_action"),
        sa.CheckConstraint("status IN ('proposed','approved','rejected','cancelled','executing','completed','failed','expired')", name="ck_proposal_status"),
        sa.CheckConstraint("version >= 1 AND warning_count >= 0", name="ck_proposal_counters"),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_proposal_hash"))
    op.create_index("ix_agent_action_proposals_agent_run_id", "agent_action_proposals", ["agent_run_id"])
    op.create_index("ix_proposal_scope_status", "agent_action_proposals", ["household_id", "status"])
    op.create_table("agent_approval_events", sa.Column("id", sa.Uuid(), primary_key=True), proposal(), home(),
        sa.Column("event_type", sa.String(32), nullable=False), sa.Column("previous_status", sa.String(16)),
        sa.Column("new_status", sa.String(16), nullable=False), sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False), sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64)), sa.Column("safe_metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for column in ("proposal_id", "household_id"):
        op.create_index(f"ix_agent_approval_events_{column}", "agent_approval_events", [column])
    op.create_table("agent_action_executions", sa.Column("id", sa.Uuid(), primary_key=True), proposal(), home(),
        sa.Column("action_type", sa.String(32), nullable=False), sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False), sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("grocery_list_id", sa.Uuid()),
        sa.Column("result_summary_json", sa.JSON(), nullable=False), sa.Column("failure_code", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("proposal_id", name="uq_execution_proposal"),
        sa.UniqueConstraint("household_id", "idempotency_key", name="uq_execution_key"),
        sa.CheckConstraint("action_type = 'create_grocery_list'", name="ck_execution_action"),
        sa.CheckConstraint("status IN ('executing','completed','failed')", name="ck_execution_status"))
    op.create_index("ix_agent_action_executions_household_id", "agent_action_executions", ["household_id"])


def downgrade():
    for table in ("agent_action_executions", "agent_approval_events", "agent_action_proposals", "agent_run_snapshots"):
        op.drop_table(table)
