"""backfill bio_blog_workflow_id from legacy blog_workflow_id

Revision ID: q4r5s6t7u8v9
Revises: p3q4r5s6t7u8
Create Date: 2026-06-27
"""
from alembic import op


revision = "q4r5s6t7u8v9"
down_revision = "p3q4r5s6t7u8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE trending_keywords
        SET bio_blog_workflow_id = blog_workflow_id
        WHERE blog_workflow_id IS NOT NULL
          AND bio_blog_workflow_id IS NULL
        """
    )


def downgrade() -> None:
    pass
