from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.trending import KeywordCoverageInfo


class RedditSubredditConfig(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    keywords: List[str] = Field(default_factory=list)
    enabled: bool = True
    max_posts: Optional[int] = Field(default=None, ge=1, le=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if value.lower().startswith("r/"):
            value = value[2:]
        if not value or not all(character.isalnum() or character == "_" for character in value):
            raise ValueError("Use a valid subreddit name without spaces or URL characters")
        return value


class RedditTrendSettingsResponse(BaseModel):
    enabled: bool
    interval_hours: Literal[6, 12]
    queries: List[str]
    subreddits: List[RedditSubredditConfig]
    keyword_max_posts: int
    subreddit_max_posts: int
    daily_item_limit: int
    monthly_item_limit: int
    freshness_hours: int
    next_run_at: Optional[datetime] = None


class RedditTrendSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_hours: Optional[Literal[6, 12]] = None
    queries: Optional[List[str]] = Field(default=None, max_length=25)
    subreddits: Optional[List[RedditSubredditConfig]] = Field(default=None, max_length=25)
    keyword_max_posts: Optional[int] = Field(default=None, ge=1, le=50)
    subreddit_max_posts: Optional[int] = Field(default=None, ge=1, le=50)
    daily_item_limit: Optional[int] = Field(default=None, ge=1, le=100000)
    monthly_item_limit: Optional[int] = Field(default=None, ge=1, le=1000000)
    freshness_hours: Optional[int] = Field(default=None, ge=1, le=168)


class RedditRunStartRequest(BaseModel):
    force: bool = False


class RedditRunResponse(BaseModel):
    id: int
    status: str
    stage: str
    trigger: str
    force_refresh: bool
    items_collected: int
    posts_accepted: int
    clusters_created: int
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cached: bool = False
    message: str = ""


class RedditSourcePostResponse(BaseModel):
    id: int
    reddit_post_id: str
    title: str
    canonical_url: str
    subreddit: str
    author: Optional[str] = None
    score: int
    comments: int
    score_per_hour: float
    comments_per_hour: float
    upvote_ratio: float
    created_utc: datetime
    retrieved_at: datetime


class RedditTrendResponse(BaseModel):
    id: int
    keyword_id: int
    topic: str
    keyword: str
    summary: Optional[str] = None
    category: Optional[str] = None
    topic_type: Optional[str] = None
    primary_subreddit: Optional[str] = None
    subreddit_mentions: int
    post_score: int
    comments: int
    score_per_hour: float
    comments_per_hour: float
    upvote_ratio: float
    trend_score: float
    post_age_hours: Optional[float] = None
    source_url: Optional[str] = None
    timestamp: Optional[datetime] = None
    status: str
    converted: bool
    kd: Optional[int] = None
    blog_workflow_id: Optional[int] = None
    coverage: Optional[KeywordCoverageInfo] = None


class RedditTrendListResponse(BaseModel):
    trends: List[RedditTrendResponse]
    count: int
    page: int
    page_size: int
    last_run: Optional[RedditRunResponse] = None
    last_successful_at: Optional[datetime] = None


class RedditTrendStatusUpdate(BaseModel):
    status: Literal["active", "ignored", "archived"]


class RedditBulkActionRequest(BaseModel):
    trend_ids: List[int] = Field(min_length=1, max_length=100)
    action: Literal["convert", "archive", "ignore", "restore"]


class RedditBulkActionResponse(BaseModel):
    updated: int
    message: str
