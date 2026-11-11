"""add news topic sources and daily blog fields

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
"""

import sqlalchemy as sa
from alembic import op

revision = "n8o9p0q1r2s3"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_llm_settings", sa.Column("news_topic_sources", sa.JSON(), nullable=True))
    op.add_column("user_llm_settings", sa.Column("news_topics", sa.JSON(), nullable=True))
    op.add_column(
        "user_llm_settings",
        sa.Column(
            "news_daily_blog_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "user_llm_settings",
        sa.Column("news_wordpress_site_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_llm_settings", "news_wordpress_site_id")
    op.drop_column("user_llm_settings", "news_daily_blog_enabled")
    op.drop_column("user_llm_settings", "news_topics")
    op.drop_column("user_llm_settings", "news_topic_sources")
