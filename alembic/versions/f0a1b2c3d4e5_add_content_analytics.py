"""add content analytics

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
"""

from alembic import op
import sqlalchemy as sa


revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _column_exists(table: str, name: str) -> bool:
    return name in {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _index_exists(table: str, name: str) -> bool:
    return name in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _foreign_key_exists(table: str, name: str) -> bool:
    return name in {key.get("name") for key in sa.inspect(op.get_bind()).get_foreign_keys(table)}


def _create_table_if_missing(name: str, *columns) -> None:
    if not _table_exists(name):
        op.create_table(name, *columns)


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    if not _index_exists(table, name):
        op.create_index(name, table, columns)


def upgrade() -> None:
    for name, column in (
        ("target_keyword", sa.Column("target_keyword", sa.String(512))),
        ("source_category", sa.Column("source_category", sa.String(255))),
        ("wp_author_id", sa.Column("wp_author_id", sa.Integer())),
        ("wp_author_name", sa.Column("wp_author_name", sa.String(255))),
        ("canonical_url", sa.Column("canonical_url", sa.Text())),
        ("remote_status", sa.Column("remote_status", sa.String(32))),
        ("wordpress_published_at", sa.Column("wordpress_published_at", sa.DateTime(timezone=True))),
        ("last_verified_at", sa.Column("last_verified_at", sa.DateTime(timezone=True))),
    ):
        if not _column_exists("published_posts", name):
            op.add_column("published_posts", column)

    if not _column_exists("social_post_history", "related_published_post_id"):
        op.add_column("social_post_history", sa.Column("related_published_post_id", sa.Integer()))
    if not _foreign_key_exists("social_post_history", "fk_social_post_history_related_published_post"):
        op.create_foreign_key(
            "fk_social_post_history_related_published_post",
            "social_post_history", "published_posts",
            ["related_published_post_id"], ["id"], ondelete="SET NULL",
        )
    if not _column_exists("social_post_publications", "social_account_id"):
        op.add_column("social_post_publications", sa.Column("social_account_id", sa.Integer()))
    if not _column_exists("social_post_publications", "remote_status"):
        op.add_column("social_post_publications", sa.Column("remote_status", sa.String(32)))
    if not _column_exists("social_post_publications", "last_verified_at"):
        op.add_column("social_post_publications", sa.Column("last_verified_at", sa.DateTime(timezone=True)))
    if not _foreign_key_exists("social_post_publications", "fk_social_post_publications_social_account"):
        op.create_foreign_key(
            "fk_social_post_publications_social_account",
            "social_post_publications", "social_accounts",
            ["social_account_id"], ["id"], ondelete="SET NULL",
        )

    _create_table_if_missing(
        "blog_publication_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_id", sa.Integer(), sa.ForeignKey("workflow_states.id", ondelete="CASCADE"), nullable=False),
        sa.Column("wordpress_site_id", sa.Integer(), sa.ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("published_post_id", sa.Integer(), sa.ForeignKey("published_posts.id", ondelete="SET NULL")),
        sa.Column("blog_key", sa.String(20), nullable=False, server_default="parent"),
        sa.Column("requested_status", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _create_index_if_missing("ix_blog_publication_attempts_user_date", "blog_publication_attempts", ["user_id", "attempted_at"])

    _create_table_if_missing(
        "social_publication_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("history_id", sa.Integer(), sa.ForeignKey("social_post_history.id", ondelete="CASCADE"), nullable=False),
        sa.Column("social_account_id", sa.Integer(), sa.ForeignKey("social_accounts.id", ondelete="SET NULL")),
        sa.Column("publication_id", sa.Integer(), sa.ForeignKey("social_post_publications.id", ondelete="SET NULL")),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(256)),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _create_index_if_missing("ix_social_publication_attempts_user_date", "social_publication_attempts", ["user_id", "attempted_at"])

    _create_table_if_missing(
        "ga_oauth_tokens",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("access_token", sa.Text()), sa.Column("refresh_token", sa.Text()),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)), sa.Column("account_email", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _create_table_if_missing(
        "ga_properties",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("wordpress_site_id", sa.Integer(), sa.ForeignKey("wordpress_sites.id", ondelete="SET NULL")),
        sa.Column("property_id", sa.String(64), nullable=False), sa.Column("display_name", sa.String(255)),
        sa.Column("property_timezone", sa.String(64)), sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "property_id", name="uq_ga_property_user_property"),
    )
    _create_index_if_missing("ix_ga_properties_user_id", "ga_properties", ["user_id"])
    _create_table_if_missing(
        "ga_page_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.Integer(), sa.ForeignKey("ga_properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False), sa.Column("page_path", sa.Text(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("organic_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("property_id", "date", "page_path", name="uq_ga_page_metric"),
    )
    _create_index_if_missing("ix_ga_page_metrics_property_id", "ga_page_metrics", ["property_id"])

    _create_table_if_missing(
        "social_post_metric_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("publication_id", sa.Integer(), sa.ForeignKey("social_post_publications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("social_account_id", sa.Integer(), sa.ForeignKey("social_accounts.id", ondelete="SET NULL")),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        *[sa.Column(name, sa.Integer()) for name in ("impressions", "reach", "likes", "reactions", "comments", "shares", "reposts", "link_clicks")],
        sa.Column("availability", sa.JSON()), sa.Column("raw_metrics", sa.JSON()),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("publication_id", "snapshot_date", name="uq_social_post_metric_snapshot_day"),
    )
    _create_index_if_missing("ix_social_post_metric_snapshots_user_id", "social_post_metric_snapshots", ["user_id"])
    _create_index_if_missing("ix_social_post_metric_snapshots_publication_id", "social_post_metric_snapshots", ["publication_id"])

    _create_table_if_missing(
        "social_account_metric_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("social_account_id", sa.Integer(), sa.ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False), sa.Column("followers_count", sa.Integer()),
        sa.Column("availability", sa.JSON()),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("social_account_id", "snapshot_date", name="uq_social_account_metric_snapshot_day"),
    )
    _create_index_if_missing("ix_social_account_metric_snapshots_user_id", "social_account_metric_snapshots", ["user_id"])
    _create_index_if_missing("ix_social_account_metric_snapshots_social_account_id", "social_account_metric_snapshots", ["social_account_id"])

    _create_table_if_missing(
        "analytics_sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(24), nullable=False), sa.Column("scope", sa.String(32), nullable=False, server_default="all"),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("filters", sa.JSON()), sa.Column("result", sa.JSON()), sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    _create_index_if_missing("ix_analytics_sync_runs_user_status", "analytics_sync_runs", ["user_id", "status", "created_at"])
    _create_index_if_missing("ix_published_posts_user_status_date", "published_posts", ["user_id", "wp_status", "published_at"])
    _create_index_if_missing("ix_social_post_publications_user_status_date", "social_post_publications", ["user_id", "status", "published_at"])


def downgrade() -> None:
    op.drop_index("ix_social_post_publications_user_status_date", table_name="social_post_publications")
    op.drop_index("ix_published_posts_user_status_date", table_name="published_posts")
    for table in (
        "analytics_sync_runs", "social_account_metric_snapshots", "social_post_metric_snapshots",
        "ga_page_metrics", "ga_properties", "ga_oauth_tokens", "social_publication_attempts",
        "blog_publication_attempts",
    ):
        op.drop_table(table)
    op.drop_constraint("fk_social_post_publications_social_account", "social_post_publications", type_="foreignkey")
    for column in ("last_verified_at", "remote_status", "social_account_id"):
        op.drop_column("social_post_publications", column)
    op.drop_constraint("fk_social_post_history_related_published_post", "social_post_history", type_="foreignkey")
    op.drop_column("social_post_history", "related_published_post_id")
    for column in (
        "last_verified_at", "wordpress_published_at", "remote_status", "canonical_url",
        "wp_author_name", "wp_author_id", "source_category", "target_keyword",
    ):
        op.drop_column("published_posts", column)
