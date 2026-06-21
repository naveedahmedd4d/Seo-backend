"""add social_post_template_prefs table

Revision ID: j7k8l9m0n1o2
Revises: i6j7k8l9m0n1
Create Date: 2026-06-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "j7k8l9m0n1o2"
down_revision = "i6j7k8l9m0n1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "social_post_template_prefs" in inspector.get_table_names():
        return
    op.create_table(
        "social_post_template_prefs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("store", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("social_post_template_prefs")
