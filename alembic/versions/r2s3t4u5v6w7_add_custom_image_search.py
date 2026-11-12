"""add custom image search API settings

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
"""

from alembic import op
import sqlalchemy as sa


revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "user_llm_settings" not in tables:
        return
    columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
    if "custom_image_search_api_key" not in columns:
        op.add_column(
            "user_llm_settings",
            sa.Column("custom_image_search_api_key", sa.Text(), nullable=True),
        )
    if "custom_image_search_base_url" not in columns:
        op.add_column(
            "user_llm_settings",
            sa.Column("custom_image_search_base_url", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "user_llm_settings" not in tables:
        return
    columns = {c["name"] for c in inspector.get_columns("user_llm_settings")}
    for name in ("custom_image_search_base_url", "custom_image_search_api_key"):
        if name in columns:
            op.drop_column("user_llm_settings", name)
