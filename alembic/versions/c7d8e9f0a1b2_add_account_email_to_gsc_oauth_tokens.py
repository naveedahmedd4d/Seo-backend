"""add account_email to gsc_oauth_tokens

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("gsc_oauth_tokens")}

    if "account_email" not in columns:
        op.add_column(
            "gsc_oauth_tokens",
            sa.Column("account_email", sa.String(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("gsc_oauth_tokens")}

    if "account_email" in columns:
        op.drop_column("gsc_oauth_tokens", "account_email")
