"""add authority content columns and published nodes

Revision ID: m0n1o2p3q4r5
Revises: l9m0n1o2p3q4
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "m0n1o2p3q4r5"
down_revision = "l9m0n1o2p3q4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "authority_sites" not in inspector.get_table_names():
        return

    cols = {c["name"] for c in inspector.get_columns("authority_sites")}
    if "money_page_body" not in cols:
        op.add_column("authority_sites", sa.Column("money_page_body", sa.Text(), nullable=True))
        op.add_column("authority_sites", sa.Column("money_page_meta_description", sa.Text(), nullable=True))
        op.add_column("authority_sites", sa.Column("money_page_seo_title", sa.String(), nullable=True))
        op.add_column("authority_sites", sa.Column("money_page_wp_page_id", sa.Integer(), nullable=True))
        op.add_column("authority_sites", sa.Column("money_page_wp_link", sa.Text(), nullable=True))
        op.add_column("authority_sites", sa.Column("money_page_generation_error", sa.Text(), nullable=True))
        op.add_column(
            "authority_sites",
            sa.Column("money_page_is_generating", sa.Boolean(), nullable=False, server_default="false"),
        )

    cluster_cols = {c["name"] for c in inspector.get_columns("authority_service_clusters")}
    if "body" not in cluster_cols:
        op.add_column("authority_service_clusters", sa.Column("body", sa.Text(), nullable=True))
        op.add_column("authority_service_clusters", sa.Column("meta_description", sa.Text(), nullable=True))
        op.add_column("authority_service_clusters", sa.Column("seo_title", sa.String(), nullable=True))
        op.add_column("authority_service_clusters", sa.Column("wp_page_id", sa.Integer(), nullable=True))
        op.add_column("authority_service_clusters", sa.Column("wp_link", sa.Text(), nullable=True))
        op.add_column("authority_service_clusters", sa.Column("generation_error", sa.Text(), nullable=True))
        op.add_column(
            "authority_service_clusters",
            sa.Column("is_generating", sa.Boolean(), nullable=False, server_default="false"),
        )

    blog_cols = {c["name"] for c in inspector.get_columns("authority_blog_posts")}
    if "body" not in blog_cols:
        op.add_column("authority_blog_posts", sa.Column("body", sa.Text(), nullable=True))
        op.add_column("authority_blog_posts", sa.Column("meta_description", sa.Text(), nullable=True))
        op.add_column("authority_blog_posts", sa.Column("seo_title", sa.String(), nullable=True))
        op.add_column("authority_blog_posts", sa.Column("wp_post_id", sa.Integer(), nullable=True))
        op.add_column("authority_blog_posts", sa.Column("wp_link", sa.Text(), nullable=True))
        op.add_column("authority_blog_posts", sa.Column("generation_error", sa.Text(), nullable=True))
        op.add_column(
            "authority_blog_posts",
            sa.Column("is_generating", sa.Boolean(), nullable=False, server_default="false"),
        )

    if "authority_published_nodes" not in inspector.get_table_names():
        op.create_table(
            "authority_published_nodes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("authority_site_id", sa.Integer(), nullable=False),
            sa.Column("wordpress_site_id", sa.Integer(), nullable=False),
            sa.Column("node_type", sa.String(length=20), nullable=False),
            sa.Column("node_id", sa.Integer(), nullable=False),
            sa.Column("wp_content_type", sa.String(length=10), nullable=False),
            sa.Column("wp_content_id", sa.Integer(), nullable=False),
            sa.Column("wp_link", sa.Text(), nullable=True),
            sa.Column("wp_status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("title", sa.Text(), nullable=True),
            sa.Column(
                "published_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["authority_site_id"], ["authority_sites.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["wordpress_site_id"], ["wordpress_sites.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "node_type", "node_id", "wordpress_site_id", name="uq_authority_published_node"
            ),
        )
        op.create_index("ix_authority_published_nodes_id", "authority_published_nodes", ["id"])
        op.create_index(
            "ix_authority_published_nodes_authority_site_id",
            "authority_published_nodes",
            ["authority_site_id"],
        )


def downgrade() -> None:
    op.drop_table("authority_published_nodes")
    for table, cols in [
        ("authority_blog_posts", ["body", "meta_description", "seo_title", "wp_post_id", "wp_link", "generation_error", "is_generating"]),
        ("authority_service_clusters", ["body", "meta_description", "seo_title", "wp_page_id", "wp_link", "generation_error", "is_generating"]),
        ("authority_sites", ["money_page_body", "money_page_meta_description", "money_page_seo_title", "money_page_wp_page_id", "money_page_wp_link", "money_page_generation_error", "money_page_is_generating"]),
    ]:
        for col in cols:
            op.drop_column(table, col)
