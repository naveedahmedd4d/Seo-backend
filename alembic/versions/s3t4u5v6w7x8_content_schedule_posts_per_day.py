"""add social/blog posts_per_day for content schedule

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
"""

from alembic import op
import sqlalchemy as sa


revision = "s3t4u5v6w7x8"
down_revision = "r2s3t4u5v6w7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "weekly_content_settings" not in tables:
        return
    columns = {c["name"] for c in inspector.get_columns("weekly_content_settings")}
    if "social_posts_per_day" not in columns:
        op.add_column(
            "weekly_content_settings",
            sa.Column(
                "social_posts_per_day",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )
    if "blog_posts_per_day" not in columns:
        op.add_column(
            "weekly_content_settings",
            sa.Column(
                "blog_posts_per_day",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "weekly_content_settings" not in tables:
        return
    columns = {c["name"] for c in inspector.get_columns("weekly_content_settings")}
    for name in ("blog_posts_per_day", "social_posts_per_day"):
        if name in columns:
            op.drop_column("weekly_content_settings", name)
