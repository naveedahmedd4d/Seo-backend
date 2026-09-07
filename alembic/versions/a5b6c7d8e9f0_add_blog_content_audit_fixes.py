"""add blog_content_audit_fixes

Revision ID: a5b6c7d8e9f0
Revises: a4b5c6d7e8f9
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "a5b6c7d8e9f0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "blog_content_audit_fixes" not in tables:
        op.create_table(
            "blog_content_audit_fixes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("audit_id", sa.Integer(), nullable=False),
            sa.Column("fix_type", sa.String(length=64), nullable=False),
            sa.Column("target_field", sa.String(length=32), nullable=False, server_default="content"),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("severity", sa.String(length=32), nullable=False, server_default="medium"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="proposed"),
            sa.Column("finding_refs", sa.JSON(), nullable=True),
            sa.Column("before_text", sa.Text(), nullable=True),
            sa.Column("after_proposed", sa.Text(), nullable=True),
            sa.Column("diff_segments", sa.JSON(), nullable=True),
            sa.Column("generator_meta", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["audit_id"], ["blog_content_audits.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_blog_content_audit_fixes_id", "blog_content_audit_fixes", ["id"])
        op.create_index("ix_blog_content_audit_fixes_audit_id", "blog_content_audit_fixes", ["audit_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "blog_content_audit_fixes" in tables:
        op.drop_index("ix_blog_content_audit_fixes_audit_id", table_name="blog_content_audit_fixes")
        op.drop_index("ix_blog_content_audit_fixes_id", table_name="blog_content_audit_fixes")
        op.drop_table("blog_content_audit_fixes")
