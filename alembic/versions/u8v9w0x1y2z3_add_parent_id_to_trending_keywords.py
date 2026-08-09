"""add parent_id to trending_keywords for keyword clusters

Revision ID: u8v9w0x1y2z3
Revises: t7u8v9w0x1y2
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "u8v9w0x1y2z3"
down_revision = "t7u8v9w0x1y2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("trending_keywords")}

    if "parent_id" not in columns:
        op.add_column(
            "trending_keywords",
            sa.Column("parent_id", sa.Integer(), nullable=True),
        )
        op.create_index(
            "ix_trending_keywords_parent_id",
            "trending_keywords",
            ["parent_id"],
        )
        op.create_foreign_key(
            "fk_trending_keywords_parent_id",
            "trending_keywords",
            "trending_keywords",
            ["parent_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("trending_keywords")}

    if "parent_id" in columns:
        op.drop_constraint(
            "fk_trending_keywords_parent_id",
            "trending_keywords",
            type_="foreignkey",
        )
        op.drop_index("ix_trending_keywords_parent_id", table_name="trending_keywords")
        op.drop_column("trending_keywords", "parent_id")
