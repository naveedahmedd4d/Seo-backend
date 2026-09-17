"""add user_id to trending_keywords for per-account isolation

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("trending_keywords")}
    indexes = {i["name"] for i in inspector.get_indexes("trending_keywords")}
    fks = {fk["name"] for fk in inspector.get_foreign_keys("trending_keywords")}

    if "user_id" not in columns:
        # Nullable: pre-existing rows have no known owner and are intentionally left
        # unassigned (they become invisible to every account until reassigned/cleaned
        # up by an admin) rather than guessed at, since guessing would just move the
        # cross-account leak instead of fixing it.
        op.add_column(
            "trending_keywords",
            sa.Column("user_id", sa.Integer(), nullable=True),
        )

    if "ix_trending_keywords_user_id" not in indexes:
        op.create_index(
            "ix_trending_keywords_user_id",
            "trending_keywords",
            ["user_id"],
        )

    if "fk_trending_keywords_user_id" not in fks:
        op.create_foreign_key(
            "fk_trending_keywords_user_id",
            "trending_keywords",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("trending_keywords")}
    indexes = {i["name"] for i in inspector.get_indexes("trending_keywords")}
    fks = {fk["name"] for fk in inspector.get_foreign_keys("trending_keywords")}

    if "fk_trending_keywords_user_id" in fks:
        op.drop_constraint(
            "fk_trending_keywords_user_id",
            "trending_keywords",
            type_="foreignkey",
        )
    if "ix_trending_keywords_user_id" in indexes:
        op.drop_index("ix_trending_keywords_user_id", table_name="trending_keywords")
    if "user_id" in columns:
        op.drop_column("trending_keywords", "user_id")
