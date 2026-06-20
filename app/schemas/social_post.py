from pydantic import BaseModel
from typing import List, Optional, Any


class SocialPostGenerateRequest(BaseModel):
    query: str
    template_variant: Optional[str] = None


class NewsResultItem(BaseModel):
    title: str
    link: str
    source: str
    snippet: str
    date: str


class SocialPostGenerateResponse(BaseModel):
    headline: str
    news_results: List[NewsResultItem]
    word_count: int
    char_count: int
    estimated_engagement_score: str  # "High" | "Medium" | "Low"
    suggested_platforms: List[str]
    hashtags: List[str]
    summary: str
    description: Optional[str] = None
    story_description: Optional[str] = None


class SocialPostHistorySaveRequest(BaseModel):
    query: str
    headline: str
    description: Optional[str] = None
    story_description: Optional[str] = None
    hashtags: Optional[List[str]] = None
    settings_snapshot: Optional[dict[str, Any]] = None
    news_results: Optional[List[dict[str, Any]]] = None


class SocialPostHistoryItemResponse(BaseModel):
    id: int
    query: str
    headline: str
    description: Optional[str] = None
    story_description: Optional[str] = None
    hashtags: Optional[List[str]] = None
    settings_snapshot: Optional[dict[str, Any]] = None
    news_results: Optional[List[dict[str, Any]]] = None
    created_at: str

    class Config:
        from_attributes = True


class SocialPostPublishPlatformOptions(BaseModel):
    pinterest: Optional[dict[str, Any]] = None
    blogger: Optional[dict[str, Any]] = None
    reddit: Optional[dict[str, Any]] = None


class SocialPostPublishRequest(BaseModel):
    platforms: List[str]
    headline: str
    description: Optional[str] = None
    story_description: Optional[str] = None
    source_url: Optional[str] = None
    hashtags: Optional[List[str]] = None
    image_base64: Optional[str] = None
    platform_options: Optional[SocialPostPublishPlatformOptions] = None


class SocialPostPublishResultItem(BaseModel):
    platform: str
    success: bool
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    error: Optional[str] = None


class SocialPostPublishResponse(BaseModel):
    results: List[SocialPostPublishResultItem]


class SocialPostImageUploadRequest(BaseModel):
    image_base64: str


class SocialPostImageUploadResponse(BaseModel):
    url: str
    filename: str
