"""add social post publish pipeline columns and publications table

Revision ID: r5s6t7u8v9w0
Revises: q4r5s6t7u8v9
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "r5s6t7u8v9w0"
down_revision = "q4r5s6t7u8v9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    history_cols = {c["name"] for c in inspector.get_columns("social_post_history")}

    if "media_filename" not in history_cols:
        op.add_column("social_post_history", sa.Column("media_filename", sa.String(length=512), nullable=True))
    if "publish_status" not in history_cols:
        op.add_column(
            "social_post_history",
            sa.Column("publish_status", sa.String(length=32), server_default="draft", nullable=False),
        )
    if "published_at" not in history_cols:
        op.add_column("social_post_history", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    if "updated_at" not in history_cols:
        op.add_column(
            "social_post_history",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
        )

    if "social_post_publications" not in inspector.get_table_names():
        op.create_table(
            "social_post_publications",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("history_id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("external_id", sa.String(length=256), nullable=True),
            sa.Column("external_url", sa.String(length=1024), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["history_id"], ["social_post_history.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("history_id", "platform", name="uq_social_post_publications_history_platform"),
        )

    pub_indexes = {idx["name"] for idx in inspector.get_indexes("social_post_publications")} if "social_post_publications" in inspector.get_table_names() else set()
    if "ix_social_post_publications_id" not in pub_indexes:
        op.create_index(op.f("ix_social_post_publications_id"), "social_post_publications", ["id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "social_post_publications" in tables:
        pub_indexes = {idx["name"] for idx in inspector.get_indexes("social_post_publications")}
        if "ix_social_post_publications_id" in pub_indexes:
            op.drop_index(op.f("ix_social_post_publications_id"), table_name="social_post_publications")
        op.drop_table("social_post_publications")

    history_cols = {c["name"] for c in inspector.get_columns("social_post_history")}
    if "updated_at" in history_cols:
        op.drop_column("social_post_history", "updated_at")
    if "published_at" in history_cols:
        op.drop_column("social_post_history", "published_at")
    if "publish_status" in history_cols:
        op.drop_column("social_post_history", "publish_status")
    if "media_filename" in history_cols:
        op.drop_column("social_post_history", "media_filename")
