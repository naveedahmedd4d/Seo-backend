"""expand user_llm_settings with research/automation API key columns

Revision ID: y2z3a4b5c6d7
Revises: x1y2z3a4b5c6
Create Date: 2026-08-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "y2z3a4b5c6d7"
down_revision = "x1y2z3a4b5c6"
branch_labels = None
depends_on = None

_NEW_COLUMNS = (
    "trending_fallback_groq_api_key",
    "blog_fallback_groq_api_key",
    "serpapi_api_key",
    "tavily_api_key",
    "mangools_api_token",
    "apify_api_key",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "user_llm_settings" not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
    for name in _NEW_COLUMNS:
        if name not in columns:
            op.add_column("user_llm_settings", sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "user_llm_settings" not in set(inspector.get_table_names()):
        return
    columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
    for name in reversed(_NEW_COLUMNS):
        if name in columns:
            op.drop_column("user_llm_settings", name)
