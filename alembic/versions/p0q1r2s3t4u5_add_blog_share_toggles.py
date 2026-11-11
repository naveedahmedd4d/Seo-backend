"""add share_blogs_to_social and share_blogs_to_outreach toggles

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
"""

import sqlalchemy as sa
from alembic import op

revision = "p0q1r2s3t4u5"
down_revision = "o9p0q1r2s3t4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "weekly_content_settings",
        sa.Column(
            "share_blogs_to_social",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "weekly_content_settings",
        sa.Column(
            "share_blogs_to_outreach",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    # Preserve prior Goal 2 behavior (always shared to connected platforms).
    op.execute(
        """
        UPDATE weekly_content_settings
        SET share_blogs_to_social = true,
            share_blogs_to_outreach = true
        WHERE goal2_enabled = true
        """
    )


def downgrade() -> None:
    op.drop_column("weekly_content_settings", "share_blogs_to_outreach")
    op.drop_column("weekly_content_settings", "share_blogs_to_social")
