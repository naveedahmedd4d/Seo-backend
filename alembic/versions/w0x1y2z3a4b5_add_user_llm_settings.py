"""add user_llm_settings for per-user LLM keys and model prefs

Revision ID: w0x1y2z3a4b5
Revises: v9w0x1y2z3a4
Create Date: 2026-08-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "w0x1y2z3a4b5"
down_revision = "v9w0x1y2z3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "user_llm_settings" not in tables:
        op.create_table(
            "user_llm_settings",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("openrouter_api_key", sa.Text(), nullable=True),
            sa.Column("groq_api_key", sa.Text(), nullable=True),
            sa.Column(
                "preferred_provider",
                sa.String(),
                nullable=False,
                server_default="default",
            ),
            sa.Column("preferred_model", sa.String(), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "user_llm_settings" in tables:
        op.drop_table("user_llm_settings")
