from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class RedditTrendSettings(Base):
    __tablename__ = "reddit_trend_settings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    interval_hours = Column(Integer, nullable=False, default=12, server_default="12")
    queries = Column(JSON, nullable=False)
    subreddits = Column(JSON, nullable=False)
    keyword_max_posts = Column(Integer, nullable=False, default=25, server_default="25")
    subreddit_max_posts = Column(Integer, nullable=False, default=30, server_default="30")
    daily_item_limit = Column(Integer, nullable=False, default=1100, server_default="1100")
    monthly_item_limit = Column(Integer, nullable=False, default=32000, server_default="32000")
    freshness_hours = Column(Integer, nullable=False, default=12, server_default="12")
    next_run_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User")


class RedditTrendRun(Base):
    __tablename__ = "reddit_trend_runs"
    __table_args__ = (
        Index(
            "uq_reddit_trend_runs_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
            sqlite_where=text("status IN ('pending', 'running')"),
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger = Column(String(24), nullable=False, default="manual")
    status = Column(String(24), nullable=False, default="pending", index=True)
    stage = Column(String(32), nullable=False, default="queued")
    force_refresh = Column(Boolean, nullable=False, default=False)
    actor_id = Column(String(255), nullable=False)
    items_collected = Column(Integer, nullable=False, default=0)
    posts_accepted = Column(Integer, nullable=False, default=0)
    clusters_created = Column(Integer, nullable=False, default=0)
    estimated_max_items = Column(Integer, nullable=False, default=0)
    input_snapshot = Column(JSON, nullable=True)
    normalization_stats = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")
    actor_runs = relationship("RedditActorRun", back_populates="run", cascade="all, delete-orphan")


class RedditActorRun(Base):
    __tablename__ = "reddit_actor_runs"

    id = Column(Integer, primary_key=True)
    reddit_trend_run_id = Column(
        Integer, ForeignKey("reddit_trend_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_type = Column(String(24), nullable=False)
    scope_key = Column(String(255), nullable=False)
    apify_run_id = Column(String(255), nullable=True, unique=True, index=True)
    dataset_id = Column(String(255), nullable=True)
    status = Column(String(24), nullable=False, default="pending")
    item_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    run = relationship("RedditTrendRun", back_populates="actor_runs")


class RedditPost(Base):
    __tablename__ = "reddit_posts"
    __table_args__ = (
        UniqueConstraint("user_id", "reddit_post_id", name="uq_reddit_posts_user_post"),
        UniqueConstraint("user_id", "canonical_url", name="uq_reddit_posts_user_url"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reddit_post_id = Column(String(64), nullable=False, index=True)
    canonical_url = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    normalized_title = Column(Text, nullable=False, index=True)
    body_excerpt = Column(Text, nullable=True)
    author = Column(String(255), nullable=True)
    subreddit = Column(String(255), nullable=False, index=True)
    score = Column(Integer, nullable=False, default=0)
    comments = Column(Integer, nullable=False, default=0)
    score_per_hour = Column(Float, nullable=False, default=0.0)
    comments_per_hour = Column(Float, nullable=False, default=0.0)
    upvote_ratio = Column(Float, nullable=False, default=0.0)
    engagement_total = Column(Integer, nullable=False, default=0)
    created_utc = Column(DateTime(timezone=True), nullable=False, index=True)
    retrieved_at = Column(DateTime(timezone=True), nullable=False)
    raw_data = Column(JSON, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User")


class RedditTrend(Base):
    __tablename__ = "reddit_trends"
    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint", name="uq_reddit_trends_user_fingerprint"),
        UniqueConstraint("trending_keyword_id", name="uq_reddit_trends_keyword"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trending_keyword_id = Column(
        Integer, ForeignKey("trending_keywords.id", ondelete="CASCADE"), nullable=False, index=True
    )
    latest_run_id = Column(Integer, ForeignKey("reddit_trend_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    representative_post_id = Column(Integer, ForeignKey("reddit_posts.id", ondelete="SET NULL"), nullable=True)
    fingerprint = Column(String(64), nullable=False)
    topic = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    category = Column(String(255), nullable=True, index=True)
    topic_type = Column(String(100), nullable=True, index=True)
    primary_subreddit = Column(String(255), nullable=True, index=True)
    subreddit_mentions = Column(Integer, nullable=False, default=1)
    post_score = Column(Integer, nullable=False, default=0)
    comments = Column(Integer, nullable=False, default=0)
    score_per_hour = Column(Float, nullable=False, default=0.0)
    comments_per_hour = Column(Float, nullable=False, default=0.0)
    upvote_ratio = Column(Float, nullable=False, default=0.0)
    engagement_total = Column(Integer, nullable=False, default=0)
    normalized_score_per_hour = Column(Float, nullable=False, default=0.0)
    normalized_comments_per_hour = Column(Float, nullable=False, default=0.0)
    normalized_upvote_ratio = Column(Float, nullable=False, default=0.0)
    normalized_cross_subreddit = Column(Float, nullable=False, default=0.0)
    trend_score = Column(Float, nullable=False, default=0.0, index=True)
    status = Column(String(24), nullable=False, default="active", index=True)
    converted_at = Column(DateTime(timezone=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    latest_post_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User")
    keyword = relationship("TrendingKeywords")
    latest_run = relationship("RedditTrendRun")
    representative_post = relationship("RedditPost", foreign_keys=[representative_post_id])
    posts = relationship("RedditTrendPost", back_populates="trend", cascade="all, delete-orphan")


class RedditTrendPost(Base):
    __tablename__ = "reddit_trend_posts"
    __table_args__ = (
        UniqueConstraint("reddit_trend_id", "reddit_post_id", name="uq_reddit_trend_post"),
    )

    id = Column(Integer, primary_key=True)
    reddit_trend_id = Column(Integer, ForeignKey("reddit_trends.id", ondelete="CASCADE"), nullable=False, index=True)
    reddit_post_id = Column(Integer, ForeignKey("reddit_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    first_seen_run_id = Column(Integer, ForeignKey("reddit_trend_runs.id", ondelete="SET NULL"), nullable=True)
    last_seen_run_id = Column(Integer, ForeignKey("reddit_trend_runs.id", ondelete="SET NULL"), nullable=True)

    trend = relationship("RedditTrend", back_populates="posts")
    post = relationship("RedditPost")
