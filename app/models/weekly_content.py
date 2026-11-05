"""Weekly auto-content models: international events, social week, CSV blog plan."""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("external_id", "source", name="uq_calendar_events_external_source"),
        Index("ix_calendar_events_date", "event_date"),
    )

    id = Column(Integer, primary_key=True)
    event_date = Column(Date, nullable=False)
    title = Column(String(255), nullable=False)
    country_or_scope = Column(String(64), nullable=False, server_default="INT")
    source = Column(String(32), nullable=False)  # observance | nager
    external_id = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class WeeklyContentSettings(Base):
    __tablename__ = "weekly_content_settings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    goal1_enabled = Column(Boolean, nullable=False, server_default="false")
    goal2_enabled = Column(Boolean, nullable=False, server_default="false")
    countries = Column(JSON, nullable=False, default=list)  # e.g. ["US","GB","PK"]
    max_social_per_week = Column(Integer, nullable=False, server_default="7")
    blogs_per_week = Column(Integer, nullable=False, server_default="2")
    wordpress_site_id = Column(
        Integer, ForeignKey("wordpress_sites.id", ondelete="SET NULL"), nullable=True
    )
    cron_weekday = Column(Integer, nullable=False, server_default="0")  # 0=Monday
    cron_time_utc = Column(String(8), nullable=False, server_default="09:00")
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_run_iso_week = Column(String(16), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    wordpress_site = relationship("WordPressSite")


class WeeklyContentRun(Base):
    """Idempotent weekly run per user (covers Goal 1 + Goal 2)."""

    __tablename__ = "weekly_content_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "iso_week", name="uq_weekly_content_runs_user_week"),
        Index("ix_weekly_content_runs_status", "status"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    iso_week = Column(String(16), nullable=False)  # e.g. 2026-W45
    status = Column(String(32), nullable=False, server_default="queued")
    trigger = Column(String(24), nullable=False, server_default="scheduled")  # scheduled | manual
    error_summary = Column(Text, nullable=True)
    result_summary = Column(JSON, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class WeeklySocialItem(Base):
    __tablename__ = "weekly_social_items"
    __table_args__ = (
        UniqueConstraint("user_id", "iso_week", "calendar_event_id", name="uq_weekly_social_item"),
        Index("ix_weekly_social_items_user_week", "user_id", "iso_week"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(
        Integer, ForeignKey("weekly_content_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    iso_week = Column(String(16), nullable=False)
    calendar_event_id = Column(
        Integer, ForeignKey("calendar_events.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(String(32), nullable=False, server_default="queued")
    social_post_history_id = Column(
        Integer, ForeignKey("social_post_history.id", ondelete="SET NULL"), nullable=True
    )
    publish_results = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    calendar_event = relationship("CalendarEvent")


class KeywordPlanImport(Base):
    __tablename__ = "keyword_plan_imports"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    row_count = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class KeywordPlanItem(Base):
    __tablename__ = "keyword_plan_items"
    __table_args__ = (
        Index("ix_keyword_plan_items_user_status", "user_id", "status"),
        Index("ix_keyword_plan_items_queue", "user_id", "status", "priority", "id"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    import_id = Column(
        Integer, ForeignKey("keyword_plan_imports.id", ondelete="SET NULL"), nullable=True
    )
    keyword = Column(String(255), nullable=False)
    category = Column(String(120), nullable=True)
    priority = Column(Integer, nullable=False, server_default="100")
    status = Column(String(24), nullable=False, server_default="pending")  # pending|used|skipped|failed
    iso_week = Column(String(16), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    workflow_id = Column(Integer, nullable=True)
    wordpress_post_id = Column(Integer, nullable=True)
    wordpress_link = Column(String(512), nullable=True)
    social_post_history_id = Column(Integer, nullable=True)
    result_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    import_row = relationship("KeywordPlanImport")
