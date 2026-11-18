"""add news topic niche/context for trending filters

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
"""

from alembic import op
import sqlalchemy as sa


revision = "t4u5v6w7x8y9"
down_revision = "s3t4u5v6w7x8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("user_llm_settings")}
    if "news_topic_niche" not in columns:
        op.add_column(
            "user_llm_settings",
            sa.Column("news_topic_niche", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("user_llm_settings")}
    if "news_topic_niche" in columns:
        op.drop_column("user_llm_settings", "news_topic_niche")
