"""add Claude LLM relay settings

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
"""

from alembic import op
import sqlalchemy as sa


revision = "q1r2s3t4u5v6"
down_revision = "p0q1r2s3t4u5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "user_llm_settings" not in tables:
        return
    columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
    if "claude_llm_api_key" not in columns:
        op.add_column("user_llm_settings", sa.Column("claude_llm_api_key", sa.Text(), nullable=True))
    if "claude_llm_base_url" not in columns:
        op.add_column("user_llm_settings", sa.Column("claude_llm_base_url", sa.Text(), nullable=True))
    if "claude_llm_webhook_secret" not in columns:
        op.add_column(
            "user_llm_settings",
            sa.Column("claude_llm_webhook_secret", sa.Text(), nullable=True),
        )
    if "claude_llm_timeout_ms" not in columns:
        op.add_column(
            "user_llm_settings",
            sa.Column("claude_llm_timeout_ms", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "user_llm_settings" not in tables:
        return
    columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
    for name in (
        "claude_llm_timeout_ms",
        "claude_llm_webhook_secret",
        "claude_llm_base_url",
        "claude_llm_api_key",
    ):
        if name in columns:
            op.drop_column("user_llm_settings", name)
