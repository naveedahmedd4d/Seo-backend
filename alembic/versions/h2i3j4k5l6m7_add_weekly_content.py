"""add weekly content tables

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
"""

from alembic import op
import sqlalchemy as sa


revision = "h2i3j4k5l6m7"
down_revision = "g1h2i3j4k5l6"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _table_exists("calendar_events"):
        op.create_table(
            "calendar_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("event_date", sa.Date(), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("country_or_scope", sa.String(64), nullable=False, server_default="INT"),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("external_id", sa.String(160), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("external_id", "source", name="uq_calendar_events_external_source"),
        )
        op.create_index("ix_calendar_events_date", "calendar_events", ["event_date"])

    if not _table_exists("weekly_content_settings"):
        op.create_table(
            "weekly_content_settings",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("goal1_enabled", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("goal2_enabled", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("countries", sa.JSON(), nullable=False),
            sa.Column("max_social_per_week", sa.Integer(), nullable=False, server_default="7"),
            sa.Column("blogs_per_week", sa.Integer(), nullable=False, server_default="2"),
            sa.Column(
                "wordpress_site_id",
                sa.Integer(),
                sa.ForeignKey("wordpress_sites.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("cron_weekday", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cron_time_utc", sa.String(8), nullable=False, server_default="09:00"),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_run_iso_week", sa.String(16), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    if not _table_exists("weekly_content_runs"):
        op.create_table(
            "weekly_content_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("iso_week", sa.String(16), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("trigger", sa.String(24), nullable=False, server_default="scheduled"),
            sa.Column("error_summary", sa.Text(), nullable=True),
            sa.Column("result_summary", sa.JSON(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", "iso_week", name="uq_weekly_content_runs_user_week"),
        )
        op.create_index("ix_weekly_content_runs_user_id", "weekly_content_runs", ["user_id"])
        op.create_index("ix_weekly_content_runs_status", "weekly_content_runs", ["status"])

    if not _table_exists("weekly_social_items"):
        op.create_table(
            "weekly_social_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column(
                "run_id",
                sa.Integer(),
                sa.ForeignKey("weekly_content_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("iso_week", sa.String(16), nullable=False),
            sa.Column(
                "calendar_event_id",
                sa.Integer(),
                sa.ForeignKey("calendar_events.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column(
                "social_post_history_id",
                sa.Integer(),
                sa.ForeignKey("social_post_history.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("publish_results", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "user_id", "iso_week", "calendar_event_id", name="uq_weekly_social_item"
            ),
        )
        op.create_index("ix_weekly_social_items_user_id", "weekly_social_items", ["user_id"])
        op.create_index(
            "ix_weekly_social_items_user_week", "weekly_social_items", ["user_id", "iso_week"]
        )

    if not _table_exists("keyword_plan_imports"):
        op.create_table(
            "keyword_plan_imports",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("filename", sa.String(255), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_keyword_plan_imports_user_id", "keyword_plan_imports", ["user_id"])

    if not _table_exists("keyword_plan_items"):
        op.create_table(
            "keyword_plan_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column(
                "import_id",
                sa.Integer(),
                sa.ForeignKey("keyword_plan_imports.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("keyword", sa.String(255), nullable=False),
            sa.Column("category", sa.String(120), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("iso_week", sa.String(16), nullable=True),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("workflow_id", sa.Integer(), nullable=True),
            sa.Column("wordpress_post_id", sa.Integer(), nullable=True),
            sa.Column("wordpress_link", sa.String(512), nullable=True),
            sa.Column("social_post_history_id", sa.Integer(), nullable=True),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index("ix_keyword_plan_items_user_id", "keyword_plan_items", ["user_id"])
        op.create_index(
            "ix_keyword_plan_items_user_status", "keyword_plan_items", ["user_id", "status"]
        )
        op.create_index(
            "ix_keyword_plan_items_queue",
            "keyword_plan_items",
            ["user_id", "status", "priority", "id"],
        )


def downgrade() -> None:
    for table in (
        "keyword_plan_items",
        "keyword_plan_imports",
        "weekly_social_items",
        "weekly_content_runs",
        "weekly_content_settings",
        "calendar_events",
    ):
        if _table_exists(table):
            op.drop_table(table)
