"""add GSC search console tables

Revision ID: o2p3q4r5s6t7
Revises: m0n1o2p3q4r5
Create Date: 2026-06-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "o2p3q4r5s6t7"
down_revision = "m0n1o2p3q4r5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    if "gsc_oauth_tokens" not in existing:
        op.create_table(
            "gsc_oauth_tokens",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("access_token", sa.Text(), nullable=True),
            sa.Column("refresh_token", sa.Text(), nullable=True),
            sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id"),
        )

    if "gsc_properties" not in existing:
        op.create_table(
            "gsc_properties",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("site_url", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=True),
            sa.Column("wordpress_site_id", sa.Integer(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["wordpress_site_id"], ["wordpress_sites.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_gsc_properties_id"), "gsc_properties", ["id"], unique=False)
        op.create_index(op.f("ix_gsc_properties_user_id"), "gsc_properties", ["user_id"], unique=False)

    if "gsc_page_metrics" not in existing:
        op.create_table(
            "gsc_page_metrics",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("property_id", sa.Integer(), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("page", sa.String(), nullable=False),
            sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ctr", sa.Float(), nullable=False, server_default="0"),
            sa.Column("position", sa.Float(), nullable=False, server_default="0"),
            sa.ForeignKeyConstraint(["property_id"], ["gsc_properties.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("property_id", "date", "page", name="uq_gsc_page_metric"),
        )
        op.create_index(op.f("ix_gsc_page_metrics_id"), "gsc_page_metrics", ["id"], unique=False)
        op.create_index(op.f("ix_gsc_page_metrics_property_id"), "gsc_page_metrics", ["property_id"], unique=False)

    if "gsc_query_metrics" not in existing:
        op.create_table(
            "gsc_query_metrics",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("property_id", sa.Integer(), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("query", sa.String(), nullable=False),
            sa.Column("page", sa.String(), nullable=False),
            sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ctr", sa.Float(), nullable=False, server_default="0"),
            sa.Column("position", sa.Float(), nullable=False, server_default="0"),
            sa.ForeignKeyConstraint(["property_id"], ["gsc_properties.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("property_id", "date", "query", "page", name="uq_gsc_query_metric"),
        )
        op.create_index(op.f("ix_gsc_query_metrics_id"), "gsc_query_metrics", ["id"], unique=False)
        op.create_index(op.f("ix_gsc_query_metrics_property_id"), "gsc_query_metrics", ["property_id"], unique=False)

    if "seo_action_items" not in existing:
        op.create_table(
            "seo_action_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("property_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("action_type", sa.String(length=32), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="info"),
            sa.Column("page", sa.String(), nullable=True),
            sa.Column("query", sa.String(), nullable=True),
            sa.Column("metric_snapshot", sa.JSON(), nullable=True),
            sa.Column("suggestion", sa.Text(), nullable=True),
            sa.Column("impact_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_review"),
            sa.Column("linked_published_post_id", sa.Integer(), nullable=True),
            sa.Column("linked_workflow_id", sa.Integer(), nullable=True),
            sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("user_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["property_id"], ["gsc_properties.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["linked_published_post_id"], ["published_posts.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["linked_workflow_id"], ["workflow_states.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_seo_action_items_id"), "seo_action_items", ["id"], unique=False)
        op.create_index(op.f("ix_seo_action_items_property_id"), "seo_action_items", ["property_id"], unique=False)
        op.create_index(op.f("ix_seo_action_items_user_id"), "seo_action_items", ["user_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    for table in (
        "seo_action_items",
        "gsc_query_metrics",
        "gsc_page_metrics",
        "gsc_properties",
        "gsc_oauth_tokens",
    ):
        if table in existing:
            op.drop_table(table)
