"""add bio and net worth blog workflow ids to trending_keywords

Revision ID: p3q4r5s6t7u8
Revises: n1o2p3q4r5s6
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "p3q4r5s6t7u8"
down_revision = "n1o2p3q4r5s6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("trending_keywords")}

    if "bio_blog_workflow_id" not in cols:
        op.add_column(
            "trending_keywords",
            sa.Column("bio_blog_workflow_id", sa.Integer(), nullable=True),
        )
        op.create_index(
            op.f("ix_trending_keywords_bio_blog_workflow_id"),
            "trending_keywords",
            ["bio_blog_workflow_id"],
            unique=False,
        )

    if "net_worth_blog_workflow_id" not in cols:
        op.add_column(
            "trending_keywords",
            sa.Column("net_worth_blog_workflow_id", sa.Integer(), nullable=True),
        )
        op.create_index(
            op.f("ix_trending_keywords_net_worth_blog_workflow_id"),
            "trending_keywords",
            ["net_worth_blog_workflow_id"],
            unique=False,
        )

    if "blog_workflow_id" in cols and "bio_blog_workflow_id" in cols:
        op.execute(
            """
            UPDATE trending_keywords
            SET bio_blog_workflow_id = blog_workflow_id
            WHERE blog_workflow_id IS NOT NULL
              AND bio_blog_workflow_id IS NULL
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("trending_keywords")}

    if "net_worth_blog_workflow_id" in cols:
        op.drop_index(
            op.f("ix_trending_keywords_net_worth_blog_workflow_id"),
            table_name="trending_keywords",
        )
        op.drop_column("trending_keywords", "net_worth_blog_workflow_id")

    if "bio_blog_workflow_id" in cols:
        op.drop_index(
            op.f("ix_trending_keywords_bio_blog_workflow_id"),
            table_name="trending_keywords",
        )
        op.drop_column("trending_keywords", "bio_blog_workflow_id")
