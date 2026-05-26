"""stub for existing DB revision b2c3d4e5f6a7

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: (no-op stub so Alembic can locate this revision)

Used when the database's alembic_version points to b2c3d4e5f6a7
but the migration file was missing. No schema changes.
"""
from alembic import op


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
