"""add authority builder tables

Revision ID: k8l9m0n1o2p3
Revises: j7k8l9m0n1o2
Create Date: 2026-06-25

Tables are created in the follow-up revision l9m0n1o2p3q4 (varchar status
columns). Creating Postgres ENUMs here double-fires CREATE TYPE under
SQLAlchemy 2.x, so this revision is intentionally a no-op.
"""

revision = "k8l9m0n1o2p3"
down_revision = "j7k8l9m0n1o2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
