from typing import Any, Optional

from pydantic import BaseModel, Field


class NewsSourceItem(BaseModel):
    id: str
    label: str
    description: str


class NewsTopicsSettingsUpdate(BaseModel):
    enabled_sources: Optional[list[str]] = None
    daily_blog_enabled: Optional[bool] = None
    wordpress_site_id: Optional[int] = None
    clear_wordpress_site: bool = False


class NewsTopicsSettingsResponse(BaseModel):
    sources: list[NewsSourceItem]
    enabled_sources: list[str]
    daily_blog_enabled: bool
    wordpress_site_id: Optional[int] = None
    last_run: Optional[dict[str, Any]] = None


class NewsTopicEntryItem(BaseModel):
    id: int
    date: str
    title: str
    name: str
    source: str
    link: Optional[str] = None


class NewsTopicEntriesResponse(BaseModel):
    items: list[NewsTopicEntryItem]


class NewsTopicsCronResponse(BaseModel):
    ran: int = Field(description="Users whose daily news blog was attempted")
    force: bool = False
