"""add run_frequency + last_run_date to weekly_content_settings

Lets a user's saved schedule (cron_weekday + cron_time_utc) actually gate the
run, and choose between a weekly run and a daily catch-up run.

Revision ID: m7n8o9p0q1r2
Revises: k5l6m7n8o9p0
"""

import sqlalchemy as sa
from alembic import op

revision = "m7n8o9p0q1r2"
down_revision = "k5l6m7n8o9p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "weekly_content_settings",
        sa.Column("run_frequency", sa.String(16), nullable=False, server_default="weekly"),
    )
    # Last calendar date the gate fired, so a daily run fires once per day.
    op.add_column(
        "weekly_content_settings",
        sa.Column("last_run_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("weekly_content_settings", "last_run_date")
    op.drop_column("weekly_content_settings", "run_frequency")
