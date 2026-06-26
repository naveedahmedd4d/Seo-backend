"""fix authority schema — use varchar status columns instead of postgres enums

Revision ID: l9m0n1o2p3q4
Revises: k8l9m0n1o2p3
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "l9m0n1o2p3q4"
down_revision = "k8l9m0n1o2p3"
branch_labels = None
depends_on = None

AUTHORITY_TABLES = (
    "authority_link_edges",
    "authority_blog_posts",
    "authority_service_clusters",
    "authority_sites",
)


def _drop_authority_schema() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    for table in AUTHORITY_TABLES:
        if table in existing:
            op.drop_table(table)
    op.execute("DROP TYPE IF EXISTS authoritylinkstatus CASCADE")
    op.execute("DROP TYPE IF EXISTS authoritylinktype CASCADE")
    op.execute("DROP TYPE IF EXISTS authoritynodestatus CASCADE")


def _create_authority_tables() -> None:
    op.create_table(
        "authority_sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("wordpress_site_id", sa.Integer(), nullable=False),
        sa.Column("money_page_title", sa.String(), nullable=False),
        sa.Column("money_page_slug", sa.String(), nullable=False),
        sa.Column("money_page_focus_keyword", sa.String(), nullable=False),
        sa.Column(
            "money_page_status",
            sa.String(length=20),
            nullable=False,
            server_default="planned",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wordpress_site_id"], ["wordpress_sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "wordpress_site_id", name="uq_authority_site_per_wp"),
    )
    op.create_index("ix_authority_sites_id", "authority_sites", ["id"])
    op.create_index("ix_authority_sites_user_id", "authority_sites", ["user_id"])
    op.create_index("ix_authority_sites_wordpress_site_id", "authority_sites", ["wordpress_site_id"])

    op.create_table(
        "authority_service_clusters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authority_site_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("focus_keyword", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="planned"),
        sa.Column("color", sa.String(length=20), nullable=False, server_default="blue"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["authority_site_id"], ["authority_sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_authority_service_clusters_id", "authority_service_clusters", ["id"])
    op.create_index(
        "ix_authority_service_clusters_authority_site_id",
        "authority_service_clusters",
        ["authority_site_id"],
    )

    op.create_table(
        "authority_blog_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("focus_keyword", sa.String(), nullable=False),
        sa.Column("article_type", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="planned"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["cluster_id"], ["authority_service_clusters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_authority_blog_posts_id", "authority_blog_posts", ["id"])
    op.create_index("ix_authority_blog_posts_cluster_id", "authority_blog_posts", ["cluster_id"])

    op.create_table(
        "authority_link_edges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authority_site_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("link_type", sa.String(length=30), nullable=False),
        sa.Column("anchor_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="planned"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["authority_site_id"], ["authority_sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_authority_link_edges_id", "authority_link_edges", ["id"])
    op.create_index(
        "ix_authority_link_edges_authority_site_id",
        "authority_link_edges",
        ["authority_site_id"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "authority_sites" not in tables:
        _create_authority_tables()
        return

    # Recreate broken/partial schema (empty tables — user insert failed).
    _drop_authority_schema()
    _create_authority_tables()


def downgrade() -> None:
    _drop_authority_schema()
