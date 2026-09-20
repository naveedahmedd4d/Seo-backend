"""add reddit trends

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reddit_trend_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("interval_hours", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("queries", sa.JSON(), nullable=False),
        sa.Column("subreddits", sa.JSON(), nullable=False),
        sa.Column("keyword_max_posts", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("subreddit_max_posts", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("daily_item_limit", sa.Integer(), nullable=False, server_default="1100"),
        sa.Column("monthly_item_limit", sa.Integer(), nullable=False, server_default="32000"),
        sa.Column("freshness_hours", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", name="uq_reddit_trend_settings_user"),
    )
    op.create_index("ix_reddit_trend_settings_user_id", "reddit_trend_settings", ["user_id"])
    op.create_index("ix_reddit_trend_settings_next_run_at", "reddit_trend_settings", ["next_run_at"])

    op.create_table(
        "reddit_trend_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("force_refresh", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("items_collected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("posts_accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clusters_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_max_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_snapshot", sa.JSON(), nullable=True),
        sa.Column("normalization_stats", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reddit_trend_runs_user_id", "reddit_trend_runs", ["user_id"])
    op.create_index("ix_reddit_trend_runs_status", "reddit_trend_runs", ["status"])
    op.create_index("ix_reddit_trend_runs_created_at", "reddit_trend_runs", ["created_at"])
    op.create_index(
        "uq_reddit_trend_runs_active_user",
        "reddit_trend_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
        sqlite_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "reddit_actor_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reddit_trend_run_id", sa.Integer(), sa.ForeignKey("reddit_trend_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_key", sa.String(255), nullable=False),
        sa.Column("apify_run_id", sa.String(255), nullable=True),
        sa.Column("dataset_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("apify_run_id", name="uq_reddit_actor_runs_apify_run"),
    )
    op.create_index("ix_reddit_actor_runs_reddit_trend_run_id", "reddit_actor_runs", ["reddit_trend_run_id"])
    op.create_index("ix_reddit_actor_runs_apify_run_id", "reddit_actor_runs", ["apify_run_id"])

    op.create_table(
        "reddit_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reddit_post_id", sa.String(64), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("body_excerpt", sa.Text(), nullable=True),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("subreddit", sa.String(255), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("comments_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("upvote_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("engagement_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "reddit_post_id", name="uq_reddit_posts_user_post"),
        sa.UniqueConstraint("user_id", "canonical_url", name="uq_reddit_posts_user_url"),
    )
    op.create_index("ix_reddit_posts_user_id", "reddit_posts", ["user_id"])
    op.create_index("ix_reddit_posts_reddit_post_id", "reddit_posts", ["reddit_post_id"])
    op.create_index("ix_reddit_posts_normalized_title", "reddit_posts", ["normalized_title"])
    op.create_index("ix_reddit_posts_subreddit", "reddit_posts", ["subreddit"])
    op.create_index("ix_reddit_posts_created_utc", "reddit_posts", ["created_utc"])

    op.create_table(
        "reddit_trends",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trending_keyword_id", sa.Integer(), sa.ForeignKey("trending_keywords.id", ondelete="CASCADE"), nullable=False),
        sa.Column("latest_run_id", sa.Integer(), sa.ForeignKey("reddit_trend_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("representative_post_id", sa.Integer(), sa.ForeignKey("reddit_posts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("category", sa.String(255), nullable=True),
        sa.Column("topic_type", sa.String(100), nullable=True),
        sa.Column("primary_subreddit", sa.String(255), nullable=True),
        sa.Column("subreddit_mentions", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("post_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("comments_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("upvote_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("engagement_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("normalized_score_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("normalized_comments_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("normalized_upvote_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("normalized_cross_subreddit", sa.Float(), nullable=False, server_default="0"),
        sa.Column("trend_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("latest_post_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "fingerprint", name="uq_reddit_trends_user_fingerprint"),
        sa.UniqueConstraint("trending_keyword_id", name="uq_reddit_trends_keyword"),
    )
    for column in ("user_id", "trending_keyword_id", "latest_run_id", "category", "topic_type", "primary_subreddit", "trend_score", "status", "latest_post_at"):
        op.create_index(f"ix_reddit_trends_{column}", "reddit_trends", [column])

    op.create_table(
        "reddit_trend_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reddit_trend_id", sa.Integer(), sa.ForeignKey("reddit_trends.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reddit_post_id", sa.Integer(), sa.ForeignKey("reddit_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_seen_run_id", sa.Integer(), sa.ForeignKey("reddit_trend_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_seen_run_id", sa.Integer(), sa.ForeignKey("reddit_trend_runs.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("reddit_trend_id", "reddit_post_id", name="uq_reddit_trend_post"),
    )
    op.create_index("ix_reddit_trend_posts_reddit_trend_id", "reddit_trend_posts", ["reddit_trend_id"])
    op.create_index("ix_reddit_trend_posts_reddit_post_id", "reddit_trend_posts", ["reddit_post_id"])


def downgrade() -> None:
    op.drop_table("reddit_trend_posts")
    op.drop_table("reddit_trends")
    op.drop_table("reddit_posts")
    op.drop_table("reddit_actor_runs")
    op.drop_table("reddit_trend_runs")
    op.drop_table("reddit_trend_settings")
