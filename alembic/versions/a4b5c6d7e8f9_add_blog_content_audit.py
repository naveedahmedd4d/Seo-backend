"""add blog content audit tables

Revision ID: a4b5c6d7e8f9
Revises: z3a4b5c6d7e8
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "a4b5c6d7e8f9"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "blog_content_audit_runs" not in tables:
        op.create_table(
            "blog_content_audit_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("wordpress_site_id", sa.Integer(), nullable=False),
            sa.Column("website_audit_run_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("progress_json", sa.JSON(), nullable=True),
            sa.Column("site_summary", sa.JSON(), nullable=True),
            sa.Column("scope_json", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["wordpress_site_id"], ["wordpress_sites.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["website_audit_run_id"], ["website_audit_runs.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_blog_content_audit_runs_id", "blog_content_audit_runs", ["id"])
        op.create_index("ix_blog_content_audit_runs_user_id", "blog_content_audit_runs", ["user_id"])
        op.create_index(
            "ix_blog_content_audit_runs_wordpress_site_id",
            "blog_content_audit_runs",
            ["wordpress_site_id"],
        )
        op.create_index(
            "ix_blog_content_audit_runs_website_audit_run_id",
            "blog_content_audit_runs",
            ["website_audit_run_id"],
        )

    if "blog_content_audits" not in tables:
        op.create_table(
            "blog_content_audits",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("wp_post_id", sa.Integer(), nullable=True),
            sa.Column("url", sa.String(length=2048), nullable=False),
            sa.Column("title", sa.Text(), nullable=True),
            sa.Column("slug", sa.String(length=512), nullable=True),
            sa.Column("article_type", sa.String(length=32), nullable=False, server_default="unknown"),
            sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("word_target", sa.Integer(), nullable=False, server_default="1200"),
            sa.Column("word_target_met", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("seo_score", sa.Float(), nullable=True),
            sa.Column("content_quality_score", sa.Float(), nullable=True),
            sa.Column("search_intent_score", sa.Float(), nullable=True),
            sa.Column("content_depth_score", sa.Float(), nullable=True),
            sa.Column("originality_score", sa.Float(), nullable=True),
            sa.Column("eeat_score", sa.Float(), nullable=True),
            sa.Column("adsense_quality_score", sa.Float(), nullable=True),
            sa.Column("overall_score", sa.Float(), nullable=True),
            sa.Column("findings", sa.JSON(), nullable=True),
            sa.Column("gap_matrix", sa.JSON(), nullable=True),
            sa.Column("adsense_risks", sa.JSON(), nullable=True),
            sa.Column("audit_payload", sa.JSON(), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=True),
            sa.ForeignKeyConstraint(["run_id"], ["blog_content_audit_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_blog_content_audits_id", "blog_content_audits", ["id"])
        op.create_index("ix_blog_content_audits_run_id", "blog_content_audits", ["run_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "blog_content_audits" in tables:
        op.drop_index("ix_blog_content_audits_run_id", table_name="blog_content_audits")
        op.drop_index("ix_blog_content_audits_id", table_name="blog_content_audits")
        op.drop_table("blog_content_audits")
    if "blog_content_audit_runs" in tables:
        op.drop_index(
            "ix_blog_content_audit_runs_website_audit_run_id", table_name="blog_content_audit_runs"
        )
        op.drop_index(
            "ix_blog_content_audit_runs_wordpress_site_id", table_name="blog_content_audit_runs"
        )
        op.drop_index("ix_blog_content_audit_runs_user_id", table_name="blog_content_audit_runs")
        op.drop_index("ix_blog_content_audit_runs_id", table_name="blog_content_audit_runs")
        op.drop_table("blog_content_audit_runs")
