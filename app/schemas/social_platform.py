from typing import List, Literal, Optional

from pydantic import BaseModel

SocialPlatformCategory = Literal["social", "article", "community", "messaging", "bookmark"]
SocialPlatformAuthType = Literal["oauth2", "api_key", "webhook", "none"]
SocialPlatformStatus = Literal["coming_soon", "active", "deprecated"]
SocialPlatformContentType = Literal["image_card", "link", "article"]


class SocialPlatformResponse(BaseModel):
    id: str
    name: str
    category: SocialPlatformCategory
    auth_type: SocialPlatformAuthType
    content_types: List[SocialPlatformContentType]
    status: SocialPlatformStatus
    post_api_available: bool
    connect_enabled: bool
    sort_order: int
    description: str
    docs_url: Optional[str] = None


class SocialPlatformListResponse(BaseModel):
    platforms: List[SocialPlatformResponse]
    total: int
