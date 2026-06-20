"""add social_accounts table

Revision ID: h5i6j7k8l9m0
Revises: g4h5i6j7k8l9
Create Date: 2026-06-11

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "h5i6j7k8l9m0"
down_revision = "g4h5i6j7k8l9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "social_accounts" in inspector.get_table_names():
        return

    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("account_label", sa.String(), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("account_metadata", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "platform", name="uq_social_account_user_platform"),
    )
    op.create_index(op.f("ix_social_accounts_id"), "social_accounts", ["id"], unique=False)
    op.create_index(op.f("ix_social_accounts_platform"), "social_accounts", ["platform"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "social_accounts" not in inspector.get_table_names():
        return
    op.drop_index(op.f("ix_social_accounts_platform"), table_name="social_accounts")
    op.drop_index(op.f("ix_social_accounts_id"), table_name="social_accounts")
    op.drop_table("social_accounts")
