from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

AuditStatus = Literal["pending", "running", "completed", "failed"]
AuditStage = Literal["discover", "fetch", "analyze", "score", "completed"]
IssueSeverity = Literal["critical", "high", "medium", "low"]


class WebsiteAuditStartRequest(BaseModel):
    wordpress_site_id: int
    max_pages: int = Field(default=100, ge=1, le=300)
    content_types: Optional[list[str]] = None  # e.g. ["post", "page"]


class WebsiteAuditSiteOption(BaseModel):
    id: int
    name: str
    url: str


class WebsiteAuditRunResponse(BaseModel):
    id: int
    wordpress_site_id: int
    status: str
    stage: str
    total_urls: int
    processed_urls: int
    health_score: Optional[float] = None
    category_scores: Optional[dict[str, Any]] = None
    scope_json: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class WebsiteAuditIssueResponse(BaseModel):
    id: int
    run_id: int
    page_id: Optional[int] = None
    url: str
    category: str
    issue_code: str
    title: str
    severity: str
    priority_score: float
    why_it_matters: str
    recommendation: str
    evidence: Optional[dict[str, Any]] = None

    class Config:
        from_attributes = True


class WebsiteAuditIssueListResponse(BaseModel):
    items: list[WebsiteAuditIssueResponse]
    total: int
    page: int
    page_size: int


class WebsiteAuditPageResponse(BaseModel):
    id: int
    run_id: int
    url: str
    wp_post_id: Optional[int] = None
    post_type: Optional[str] = None
    status_code: Optional[int] = None
    title: Optional[str] = None
    meta_description: Optional[str] = None
    word_count: int = 0
    thin_score: Optional[float] = None
    signals_json: Optional[dict[str, Any]] = None

    class Config:
        from_attributes = True


class WebsiteAuditPageListResponse(BaseModel):
    items: list[WebsiteAuditPageResponse]
    total: int
    page: int
    page_size: int
