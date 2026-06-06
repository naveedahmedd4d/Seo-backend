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
