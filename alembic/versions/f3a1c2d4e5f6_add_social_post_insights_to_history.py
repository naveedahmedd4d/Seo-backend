"""add social post insights to history

Revision ID: f3a1c2d4e5f6
Revises: e7f8a9b0c1d2
Create Date: 2026-05-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "f3a1c2d4e5f6"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("social_post_history")}

    with op.batch_alter_table("social_post_history") as batch:
        if "description" not in cols:
            batch.add_column(sa.Column("description", sa.Text(), nullable=True))
        if "story_description" not in cols:
            batch.add_column(sa.Column("story_description", sa.Text(), nullable=True))
        if "hashtags" not in cols:
            batch.add_column(sa.Column("hashtags", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("social_post_history") as batch:
        batch.drop_column("hashtags")
        batch.drop_column("story_description")
        batch.drop_column("description")

