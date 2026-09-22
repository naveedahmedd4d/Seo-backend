from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.database import Base


class BlogPublicationAttempt(Base):
    __tablename__ = "blog_publication_attempts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_id = Column(Integer, ForeignKey("workflow_states.id", ondelete="CASCADE"), nullable=False)
    wordpress_site_id = Column(Integer, ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=False)
    published_post_id = Column(Integer, ForeignKey("published_posts.id", ondelete="SET NULL"), nullable=True)
    blog_key = Column(String(20), nullable=False, server_default="parent")
    requested_status = Column(String(20), nullable=False)
    outcome = Column(String(16), nullable=False)  # success | failed
    error_message = Column(Text, nullable=True)
    attempted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SocialPublicationAttempt(Base):
    __tablename__ = "social_publication_attempts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    history_id = Column(Integer, ForeignKey("social_post_history.id", ondelete="CASCADE"), nullable=False)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id", ondelete="SET NULL"), nullable=True)
    publication_id = Column(Integer, ForeignKey("social_post_publications.id", ondelete="SET NULL"), nullable=True)
    platform = Column(String(32), nullable=False)
    outcome = Column(String(16), nullable=False)  # success | failed
    external_id = Column(String(256), nullable=True)
    error_message = Column(Text, nullable=True)
    attempted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class GaOAuthToken(Base):
    __tablename__ = "ga_oauth_tokens"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    account_email = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class GaProperty(Base):
    __tablename__ = "ga_properties"
    __table_args__ = (UniqueConstraint("user_id", "property_id", name="uq_ga_property_user_property"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    wordpress_site_id = Column(Integer, ForeignKey("wordpress_sites.id", ondelete="SET NULL"), nullable=True)
    property_id = Column(String(64), nullable=False)
    display_name = Column(String(255), nullable=True)
    property_timezone = Column(String(64), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class GaPageMetric(Base):
    __tablename__ = "ga_page_metrics"
    __table_args__ = (UniqueConstraint("property_id", "date", "page_path", name="uq_ga_page_metric"),)

    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("ga_properties.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    page_path = Column(Text, nullable=False)
    views = Column(Integer, nullable=False, server_default="0")
    sessions = Column(Integer, nullable=False, server_default="0")
    active_users = Column(Integer, nullable=False, server_default="0")
    organic_sessions = Column(Integer, nullable=False, server_default="0")


class SocialPostMetricSnapshot(Base):
    __tablename__ = "social_post_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("publication_id", "snapshot_date", name="uq_social_post_metric_snapshot_day"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    publication_id = Column(Integer, ForeignKey("social_post_publications.id", ondelete="CASCADE"), nullable=False, index=True)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id", ondelete="SET NULL"), nullable=True)
    snapshot_date = Column(Date, nullable=False)
    impressions = Column(Integer, nullable=True)
    reach = Column(Integer, nullable=True)
    likes = Column(Integer, nullable=True)
    reactions = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    reposts = Column(Integer, nullable=True)
    link_clicks = Column(Integer, nullable=True)
    availability = Column(JSON, nullable=True)
    raw_metrics = Column(JSON, nullable=True)
    captured_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SocialAccountMetricSnapshot(Base):
    __tablename__ = "social_account_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("social_account_id", "snapshot_date", name="uq_social_account_metric_snapshot_day"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False)
    followers_count = Column(Integer, nullable=True)
    availability = Column(JSON, nullable=True)
    captured_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AnalyticsSyncRun(Base):
    __tablename__ = "analytics_sync_runs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger = Column(String(24), nullable=False)  # manual | scheduled
    scope = Column(String(32), nullable=False, server_default="all")
    status = Column(String(24), nullable=False, server_default="queued")
    filters = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
