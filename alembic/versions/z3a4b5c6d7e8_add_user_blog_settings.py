"""add user_blog_settings and user_blog_templates

Revision ID: z3a4b5c6d7e8
Revises: y2z3a4b5c6d7
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "z3a4b5c6d7e8"
down_revision = "y2z3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "user_blog_settings" not in tables:
        op.create_table(
            "user_blog_settings",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("target_word_count", sa.Integer(), nullable=True),
            sa.Column("intro_length", sa.String(), nullable=False, server_default="standard"),
            sa.Column("conclusion_length", sa.String(), nullable=False, server_default="standard"),
            sa.Column("faq_count", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("related_query_count", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("include_site_cta", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("cta_text", sa.String(), nullable=True),
            sa.Column("cta_href", sa.String(), nullable=True),
            sa.Column("auto_featured_image", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("max_section_images", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("creativity", sa.String(), nullable=False, server_default="balanced"),
            sa.Column("custom_style_notes", sa.Text(), nullable=True),
            sa.Column("seo_power_words", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("last_updated_footer", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id"),
        )

    if "user_blog_templates" not in tables:
        op.create_table(
            "user_blog_templates",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("article_type", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("target_word_count", sa.Integer(), nullable=False),
            sa.Column("sections", sa.JSON(), nullable=False),
            sa.Column("format_options", sa.JSON(), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "article_type", name="uq_user_blog_templates_user_type"),
        )
        op.create_index("ix_user_blog_templates_id", "user_blog_templates", ["id"])
        op.create_index("ix_user_blog_templates_user_id", "user_blog_templates", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "user_blog_templates" in tables:
        op.drop_index("ix_user_blog_templates_user_id", table_name="user_blog_templates")
        op.drop_index("ix_user_blog_templates_id", table_name="user_blog_templates")
        op.drop_table("user_blog_templates")
    if "user_blog_settings" in tables:
        op.drop_table("user_blog_settings")
