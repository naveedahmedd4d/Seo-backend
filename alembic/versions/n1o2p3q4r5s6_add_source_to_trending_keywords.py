"""add source to trending_keywords

Revision ID: n1o2p3q4r5s6
Revises: o2p3q4r5s6t7
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "n1o2p3q4r5s6"
down_revision = "o2p3q4r5s6t7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("trending_keywords")}
    if "source" not in cols:
        op.add_column(
            "trending_keywords",
            sa.Column("source", sa.String(), nullable=False, server_default="trending"),
        )
    indexes = {idx["name"] for idx in inspector.get_indexes("trending_keywords")}
    if "ix_trending_keywords_source" not in indexes:
        op.create_index("ix_trending_keywords_source", "trending_keywords", ["source"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes("trending_keywords")}
    if "ix_trending_keywords_source" in indexes:
        op.drop_index("ix_trending_keywords_source", table_name="trending_keywords")
    cols = {c["name"] for c in inspector.get_columns("trending_keywords")}
    if "source" in cols:
        op.drop_column("trending_keywords", "source")
