"""add bot automation

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automation_bots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("wordpress_site_id", sa.Integer(), sa.ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("workflow_definition", sa.JSON(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("schedule_config", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_automation_bots_user_id", "automation_bots", ["user_id"])
    op.create_index("ix_automation_bots_wordpress_site_id", "automation_bots", ["wordpress_site_id"])
    op.create_index("ix_automation_bots_user_status", "automation_bots", ["user_id", "status"])
    op.create_index("ix_automation_bots_due", "automation_bots", ["status", "next_run_at"])

    op.create_table(
        "automation_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.Integer(), sa.ForeignKey("automation_bots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("wordpress_site_id", sa.Integer(), sa.ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger_type", sa.String(24), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("graph_snapshot", sa.JSON(), nullable=False),
        sa.Column("settings_snapshot", sa.JSON(), nullable=False),
        sa.Column("current_node_id", sa.String(80)),
        sa.Column("start_node_id", sa.String(80)),
        sa.Column("resumed_from_execution_id", sa.Integer(), sa.ForeignKey("automation_executions.id", ondelete="SET NULL")),
        sa.Column("error_summary", sa.Text()),
        sa.Column("final_output", sa.JSON()),
        sa.Column("idempotency_key", sa.String(160)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_automation_execution_user_key"),
    )
    op.create_index("ix_automation_executions_bot_id", "automation_executions", ["bot_id"])
    op.create_index("ix_automation_executions_user_id", "automation_executions", ["user_id"])
    op.create_index("ix_automation_executions_bot_created", "automation_executions", ["bot_id", "created_at"])
    op.create_index("ix_automation_executions_user_status", "automation_executions", ["user_id", "status"])

    op.create_table(
        "automation_node_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("automation_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(80), nullable=False),
        sa.Column("node_type", sa.String(48), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("input_data", sa.JSON()),
        sa.Column("output_data", sa.JSON()),
        sa.Column("usage_data", sa.JSON()),
        sa.Column("error_data", sa.JSON()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("execution_id", "node_id", name="uq_automation_node_execution"),
    )
    op.create_index("ix_automation_node_executions_user_id", "automation_node_executions", ["user_id"])
    op.create_index("ix_automation_node_executions_execution_position", "automation_node_executions", ["execution_id", "position"])

    op.create_table(
        "automation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("automation_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_execution_id", sa.Integer(), sa.ForeignKey("automation_node_executions.id", ondelete="CASCADE")),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("level", sa.String(16), nullable=False, server_default="info"),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_automation_events_user_id", "automation_events", ["user_id"])
    op.create_index("ix_automation_events_execution_created", "automation_events", ["execution_id", "created_at"])

    op.create_table(
        "automation_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("automation_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("execution_id", "node_id", name="uq_automation_approval_node"),
    )
    op.create_index("ix_automation_approvals_user_id", "automation_approvals", ["user_id"])

    op.create_table(
        "automation_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("workflow_definition", sa.JSON(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_automation_templates_user_id", "automation_templates", ["user_id"])
    op.create_index("ix_automation_templates_user_created", "automation_templates", ["user_id", "created_at"])

    op.create_table(
        "automation_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("automation_executions.id", ondelete="CASCADE")),
        sa.Column("channel", sa.String(24), nullable=False, server_default="in_app"),
        sa.Column("title", sa.String(220), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_automation_notifications_user_id", "automation_notifications", ["user_id"])


def downgrade() -> None:
    op.drop_table("automation_notifications")
    op.drop_table("automation_templates")
    op.drop_table("automation_approvals")
    op.drop_table("automation_events")
    op.drop_table("automation_node_executions")
    op.drop_table("automation_executions")
    op.drop_table("automation_bots")
