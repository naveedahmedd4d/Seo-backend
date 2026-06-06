"""add full_name to users

Revision ID: g4h5i6j7k8l9
Revises: f3a1c2d4e5f6
Create Date: 2026-06-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "g4h5i6j7k8l9"
down_revision = "f3a1c2d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("users")}

    if "full_name" not in columns:
        op.add_column("users", sa.Column("full_name", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("users")}

    if "full_name" in columns:
        op.drop_column("users", "full_name")
