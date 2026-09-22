"""make bot wordpress site optional

Revision ID: g1h2i3j4k5l6
Revises: f0a1b2c3d4e5
"""

from alembic import op
import sqlalchemy as sa


revision = "g1h2i3j4k5l6"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("automation_bots") as batch:
        batch.alter_column(
            "wordpress_site_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
    with op.batch_alter_table("automation_executions") as batch:
        batch.alter_column(
            "wordpress_site_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("automation_executions") as batch:
        batch.alter_column(
            "wordpress_site_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
    with op.batch_alter_table("automation_bots") as batch:
        batch.alter_column(
            "wordpress_site_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
