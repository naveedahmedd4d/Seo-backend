"""add SEO action fix and indexing fields

Revision ID: t7u8v9w0x1y2
Revises: s6t7u8v9w0x1
Create Date: 2026-07-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "t7u8v9w0x1y2"
down_revision = "s6t7u8v9w0x1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("seo_action_items")}

    if "fix_status" not in columns:
        op.add_column("seo_action_items", sa.Column("fix_status", sa.String(length=32), nullable=True))
    if "fix_result" not in columns:
        op.add_column("seo_action_items", sa.Column("fix_result", sa.JSON(), nullable=True))
    if "indexing_status" not in columns:
        op.add_column("seo_action_items", sa.Column("indexing_status", sa.String(length=32), nullable=True))
    if "gsc_inspect_url" not in columns:
        op.add_column("seo_action_items", sa.Column("gsc_inspect_url", sa.Text(), nullable=True))
    if "fixed_at" not in columns:
        op.add_column("seo_action_items", sa.Column("fixed_at", sa.DateTime(timezone=True), nullable=True))

    prop_columns = {c["name"] for c in inspector.get_columns("gsc_properties")}
    if "last_sitemap_submitted_at" not in prop_columns:
        op.add_column(
            "gsc_properties",
            sa.Column("last_sitemap_submitted_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("gsc_properties", "last_sitemap_submitted_at")
    op.drop_column("seo_action_items", "fixed_at")
    op.drop_column("seo_action_items", "gsc_inspect_url")
    op.drop_column("seo_action_items", "indexing_status")
    op.drop_column("seo_action_items", "fix_result")
    op.drop_column("seo_action_items", "fix_status")
