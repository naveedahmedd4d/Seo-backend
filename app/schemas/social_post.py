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
    related_published_post_id: Optional[int] = None


class SocialPostHistoryItemResponse(BaseModel):
    id: int
    query: str
    headline: str
    description: Optional[str] = None
    story_description: Optional[str] = None
    hashtags: Optional[List[str]] = None
    settings_snapshot: Optional[dict[str, Any]] = None
    news_results: Optional[List[dict[str, Any]]] = None
    media_filename: Optional[str] = None
    media_url: Optional[str] = None
    publish_status: Optional[str] = "draft"
    published_at: Optional[str] = None
    platforms_published: Optional[List[str]] = None
    related_published_post_id: Optional[int] = None
    created_at: str
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class SocialPostPublicationResponse(BaseModel):
    platform: str
    status: str
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    error: Optional[str] = None
    published_at: Optional[str] = None


class SocialPostHistoryDetailResponse(SocialPostHistoryItemResponse):
    publications: List[SocialPostPublicationResponse] = []


class SocialPostHistoryPublishRequest(BaseModel):
    platforms: List[str]
    image_base64: Optional[str] = None
    platform_options: Optional["SocialPostPublishPlatformOptions"] = None


class SocialPostHistoryMediaRequest(BaseModel):
    image_base64: str


class SocialPostPublishPlatformOptions(BaseModel):
    pinterest: Optional[dict[str, Any]] = None
    blogger: Optional[dict[str, Any]] = None
    reddit: Optional[dict[str, Any]] = None
    facebook: Optional[dict[str, Any]] = None
    instagram: Optional[dict[str, Any]] = None


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
    history_status: Optional[str] = None
    media_url: Optional[str] = None


class SocialPostImageUploadRequest(BaseModel):
    image_base64: str


class SocialPostImageUploadResponse(BaseModel):
    url: str
    filename: str


class SocialPostFetchImageRequest(BaseModel):
    url: str


class SocialPostFetchImageResponse(BaseModel):
    data_url: str
    content_type: str


class SocialPostTemplatePresetItem(BaseModel):
    id: str
    name: str
    settings: dict[str, Any]
    updatedAt: int


class SocialPostTemplatePresetsStore(BaseModel):
    version: int = 1
    activePresetId: str
    presets: List[SocialPostTemplatePresetItem]


class SocialPostTemplatePresetsResponse(BaseModel):
    store: Optional[SocialPostTemplatePresetsStore] = None
    updated_at: Optional[str] = None


class SocialPostTemplatePresetsSaveRequest(BaseModel):
    store: SocialPostTemplatePresetsStore
