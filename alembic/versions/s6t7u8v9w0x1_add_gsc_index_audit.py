"""add GSC index audit tables

Revision ID: s6t7u8v9w0x1
Revises: r5s6t7u8v9w0
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "s6t7u8v9w0x1"
down_revision = "r5s6t7u8v9w0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    if "gsc_index_audit_runs" not in existing:
        op.create_table(
            "gsc_index_audit_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("property_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("total_urls", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("processed_urls", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("not_indexed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("warning_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["property_id"], ["gsc_properties.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_gsc_index_audit_runs_id"), "gsc_index_audit_runs", ["id"], unique=False)
        op.create_index(
            op.f("ix_gsc_index_audit_runs_property_id"),
            "gsc_index_audit_runs",
            ["property_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_gsc_index_audit_runs_user_id"),
            "gsc_index_audit_runs",
            ["user_id"],
            unique=False,
        )

    if "gsc_url_inspections" not in existing:
        op.create_table(
            "gsc_url_inspections",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("property_id", sa.Integer(), nullable=False),
            sa.Column("url", sa.String(), nullable=False),
            sa.Column("verdict", sa.String(length=32), nullable=True),
            sa.Column("coverage_state", sa.String(length=255), nullable=True),
            sa.Column("indexing_state", sa.String(length=64), nullable=True),
            sa.Column("robots_txt_state", sa.String(length=64), nullable=True),
            sa.Column("page_fetch_state", sa.String(length=64), nullable=True),
            sa.Column("google_canonical", sa.Text(), nullable=True),
            sa.Column("user_canonical", sa.Text(), nullable=True),
            sa.Column("last_crawl_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("raw_response", sa.JSON(), nullable=True),
            sa.Column("inspected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["property_id"], ["gsc_properties.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("property_id", "url", name="uq_gsc_url_inspection"),
        )
        op.create_index(op.f("ix_gsc_url_inspections_id"), "gsc_url_inspections", ["id"], unique=False)
        op.create_index(
            op.f("ix_gsc_url_inspections_property_id"),
            "gsc_url_inspections",
            ["property_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("gsc_url_inspections")
    op.drop_table("gsc_index_audit_runs")
