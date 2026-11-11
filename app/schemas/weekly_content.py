"""Pydantic schemas for weekly auto-content APIs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class WeeklyContentSettingsResponse(BaseModel):
    goal1_enabled: bool
    goal2_enabled: bool
    countries: list[str]
    max_social_per_week: int
    blogs_per_week: int
    wordpress_site_id: Optional[int] = None
    cron_weekday: int
    cron_time_utc: str
    run_frequency: str = "weekly"
    last_run_at: Optional[datetime] = None
    last_run_iso_week: Optional[str] = None
    updated_at: Optional[datetime] = None


class WeeklyContentSettingsUpdate(BaseModel):
    goal1_enabled: Optional[bool] = None
    goal2_enabled: Optional[bool] = None
    countries: Optional[list[str]] = None
    max_social_per_week: Optional[int] = Field(default=None, ge=1, le=30)
    blogs_per_week: Optional[int] = Field(default=None, ge=1, le=10)
    wordpress_site_id: Optional[int] = None
    cron_weekday: Optional[int] = Field(default=None, ge=0, le=6)
    cron_time_utc: Optional[str] = None
    run_frequency: Optional[str] = Field(default=None, pattern="^(weekly|daily)$")


class CalendarEventItem(BaseModel):
    id: int
    event_date: date
    title: str
    country_or_scope: str
    source: str
    description: Optional[str] = None


class CalendarEventsResponse(BaseModel):
    items: list[CalendarEventItem]
    total: int


class SocialTopicCreate(BaseModel):
    topic: str = Field(..., min_length=1, max_length=255)
    publish_date: date


class SocialTopicResponse(BaseModel):
    event_id: int
    item_id: int
    topic: str
    publish_date: date
    iso_week: str
    week_bucket: str  # current | next | upcoming | past
    status: str
    source: str = "custom"


class WeeklySocialItemResponse(BaseModel):
    id: int
    iso_week: str
    status: str
    event_id: int
    event_date: date
    event_title: str
    event_scope: str
    source: Optional[str] = None
    social_post_history_id: Optional[int] = None
    headline: Optional[str] = None
    publish_results: Optional[Any] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None


class PublishEventResponse(BaseModel):
    event_id: int
    item_id: int
    iso_week: str
    status: str
    event_title: str
    error_message: Optional[str] = None
    publish_results: Optional[Any] = None


class WeeklySocialWeekResponse(BaseModel):
    iso_week: str
    items: list[WeeklySocialItemResponse]
    upcoming_events: list[CalendarEventItem]


class KeywordPlanItemResponse(BaseModel):
    id: int
    keyword: str
    category: Optional[str] = None
    priority: int
    status: str
    iso_week: Optional[str] = None
    used_at: Optional[datetime] = None
    workflow_id: Optional[int] = None
    wordpress_link: Optional[str] = None
    social_post_history_id: Optional[int] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None


class KeywordPlanListResponse(BaseModel):
    items: list[KeywordPlanItemResponse]
    total: int
    pending_count: int


class KeywordCsvUploadResponse(BaseModel):
    import_id: int
    filename: str
    row_count: int
    items_created: int


class WeeklyBlogItemResponse(BaseModel):
    id: int
    keyword: str
    category: Optional[str] = None
    status: str
    iso_week: Optional[str] = None
    workflow_id: Optional[int] = None
    wordpress_link: Optional[str] = None
    social_post_history_id: Optional[int] = None
    result_json: Optional[Any] = None
    error_message: Optional[str] = None
    used_at: Optional[datetime] = None


class WeeklyBlogWeekResponse(BaseModel):
    iso_week: str
    items: list[WeeklyBlogItemResponse]
    pending_count: int


class WeeklyRunResponse(BaseModel):
    run_id: int
    iso_week: str
    status: str
    trigger: str
    result_summary: Optional[Any] = None
    error_summary: Optional[str] = None


class WeeklyReadinessCheck(BaseModel):
    id: str
    label: str
    ok: bool
    detail: str


class WeeklyReadinessResponse(BaseModel):
    ready: bool
    iso_week: str
    goal1_ready: bool
    goal2_ready: bool
    goal1_enabled: bool
    goal2_enabled: bool
    max_social_per_week: int
    blogs_per_week: int
    social_platforms: list[str]
    outreach_platforms: list[str]
    upcoming_events_count: int
    pending_keywords: int
    wordpress_configured: bool
    wordpress_site_name: Optional[str] = None
    cron_secret_configured: bool
    last_run_at: Optional[datetime] = None
    last_run_iso_week: Optional[str] = None
    last_run_status: Optional[str] = None
    checks: list[WeeklyReadinessCheck]


class WeeklyCronRunResponse(BaseModel):
    ran: int
    iso_week: str
    force: bool
