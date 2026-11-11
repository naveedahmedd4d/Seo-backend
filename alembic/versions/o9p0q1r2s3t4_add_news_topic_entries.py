"""store each day's ranked news topics for the Trending page

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
"""

import sqlalchemy as sa
from alembic import op

revision = "o9p0q1r2s3t4"
down_revision = "n8o9p0q1r2s3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_topic_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_date", sa.Date(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_news_topic_entries_user_id", "news_topic_entries", ["user_id"])
    op.create_index("ix_news_topic_entries_topic_date", "news_topic_entries", ["topic_date"])


def downgrade() -> None:
    op.drop_index("ix_news_topic_entries_topic_date", table_name="news_topic_entries")
    op.drop_index("ix_news_topic_entries_user_id", table_name="news_topic_entries")
    op.drop_table("news_topic_entries")
