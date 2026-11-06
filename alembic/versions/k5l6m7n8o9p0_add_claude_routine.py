"""add Claude Code routine settings + jobs

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
"""

from alembic import op
import sqlalchemy as sa


revision = "k5l6m7n8o9p0"
down_revision = "j4k5l6m7n8o9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "user_llm_settings" in tables:
        columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
        for name in (
            "claude_routine_url",
            "claude_routine_token",
            "claude_routine_callback_secret",
        ):
            if name not in columns:
                op.add_column("user_llm_settings", sa.Column(name, sa.Text(), nullable=True))

    if "claude_routine_jobs" not in tables:
        op.create_table(
            "claude_routine_jobs",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="waiting"),
            sa.Column("prompt", sa.Text(), nullable=True),
            sa.Column("result_text", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("wait_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_claude_routine_jobs_user_id", "claude_routine_jobs", ["user_id"])
        op.create_index("ix_claude_routine_jobs_status", "claude_routine_jobs", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "claude_routine_jobs" in tables:
        op.drop_table("claude_routine_jobs")
    if "user_llm_settings" in tables:
        columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
        for name in (
            "claude_routine_callback_secret",
            "claude_routine_token",
            "claude_routine_url",
        ):
            if name in columns:
                op.drop_column("user_llm_settings", name)
