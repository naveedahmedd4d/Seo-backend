"""add gemini_api_key to user_llm_settings

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
"""

from alembic import op
import sqlalchemy as sa


revision = "i3j4k5l6m7n8"
down_revision = "h2i3j4k5l6m7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_llm_settings" not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
    if "gemini_api_key" not in columns:
        op.add_column("user_llm_settings", sa.Column("gemini_api_key", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_llm_settings" not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
    if "gemini_api_key" in columns:
        op.drop_column("user_llm_settings", "gemini_api_key")
