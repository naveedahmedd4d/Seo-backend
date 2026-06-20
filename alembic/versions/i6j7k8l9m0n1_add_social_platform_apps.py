"""add social_platform_apps table

Revision ID: i6j7k8l9m0n1
Revises: h5i6j7k8l9m0
Create Date: 2026-06-12

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "i6j7k8l9m0n1"
down_revision = "h5i6j7k8l9m0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "social_platform_apps" in inspector.get_table_names():
        return

    op.create_table(
        "social_platform_apps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_secret", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "platform", name="uq_social_platform_app_user_platform"),
    )
    op.create_index(op.f("ix_social_platform_apps_id"), "social_platform_apps", ["id"], unique=False)
    op.create_index(op.f("ix_social_platform_apps_platform"), "social_platform_apps", ["platform"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "social_platform_apps" not in inspector.get_table_names():
        return
    op.drop_index(op.f("ix_social_platform_apps_platform"), table_name="social_platform_apps")
    op.drop_index(op.f("ix_social_platform_apps_id"), table_name="social_platform_apps")
    op.drop_table("social_platform_apps")
