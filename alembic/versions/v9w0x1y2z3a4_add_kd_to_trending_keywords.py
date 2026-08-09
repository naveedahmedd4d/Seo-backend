"""add kd (keyword difficulty) to trending_keywords

Revision ID: v9w0x1y2z3a4
Revises: u8v9w0x1y2z3
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "v9w0x1y2z3a4"
down_revision = "u8v9w0x1y2z3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("trending_keywords")}

    if "kd" not in columns:
        op.add_column(
            "trending_keywords",
            sa.Column("kd", sa.Integer(), nullable=True),
        )
        op.create_index("ix_trending_keywords_kd", "trending_keywords", ["kd"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("trending_keywords")}

    if "kd" in columns:
        op.drop_index("ix_trending_keywords_kd", table_name="trending_keywords")
        op.drop_column("trending_keywords", "kd")
