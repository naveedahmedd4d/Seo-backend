"""add keyword_site_coverage for per-site keyword status

Revision ID: x1y2z3a4b5c6
Revises: w0x1y2z3a4b5
Create Date: 2026-08-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "x1y2z3a4b5c6"
down_revision = "w0x1y2z3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "keyword_site_coverage" not in tables:
        op.create_table(
            "keyword_site_coverage",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("wordpress_site_id", sa.Integer(), nullable=False),
            sa.Column("trending_keyword_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("matched_url", sa.String(), nullable=True),
            sa.Column("matched_title", sa.String(), nullable=True),
            sa.Column("match_method", sa.String(), nullable=True),
            sa.Column(
                "compared_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["wordpress_site_id"],
                ["wordpress_sites.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["trending_keyword_id"],
                ["trending_keywords.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "wordpress_site_id",
                "trending_keyword_id",
                name="uq_keyword_site_coverage_site_keyword",
            ),
        )
        op.create_index(
            op.f("ix_keyword_site_coverage_id"),
            "keyword_site_coverage",
            ["id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_keyword_site_coverage_wordpress_site_id"),
            "keyword_site_coverage",
            ["wordpress_site_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_keyword_site_coverage_trending_keyword_id"),
            "keyword_site_coverage",
            ["trending_keyword_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_keyword_site_coverage_status"),
            "keyword_site_coverage",
            ["status"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "keyword_site_coverage" in tables:
        op.drop_index(op.f("ix_keyword_site_coverage_status"), table_name="keyword_site_coverage")
        op.drop_index(
            op.f("ix_keyword_site_coverage_trending_keyword_id"),
            table_name="keyword_site_coverage",
        )
        op.drop_index(
            op.f("ix_keyword_site_coverage_wordpress_site_id"),
            table_name="keyword_site_coverage",
        )
        op.drop_index(op.f("ix_keyword_site_coverage_id"), table_name="keyword_site_coverage")
        op.drop_table("keyword_site_coverage")
