from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

ContentAuditStatus = Literal["pending", "running", "completed", "failed"]
ArticleType = Literal["biography", "net_worth", "news", "cluster_hub", "unknown"]
FindingSeverity = Literal["critical", "high", "medium", "low"]


class BlogContentAuditStartRequest(BaseModel):
    wordpress_site_id: int
    website_audit_run_id: Optional[int] = None
    post_limit: int = Field(default=50, ge=1, le=200)
    post_ids: Optional[list[int]] = None


class BlogContentAuditProgress(BaseModel):
    total: int = 0
    completed: int = 0
    failed: int = 0
    current_post_title: Optional[str] = None


class BlogContentAuditSiteSummary(BaseModel):
    avg_scores: Optional[dict[str, float]] = None
    overall_avg: Optional[float] = None
    adsense_risk_level: Optional[str] = None
    posts_below_word_target: int = 0
    top_gaps: list[str] = Field(default_factory=list)
    posts_audited: int = 0
    posts_failed: int = 0


class BlogContentAuditRunResponse(BaseModel):
    id: int
    wordpress_site_id: int
    website_audit_run_id: Optional[int] = None
    status: str
    progress: Optional[BlogContentAuditProgress] = None
    site_summary: Optional[BlogContentAuditSiteSummary] = None
    scope_json: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BlogContentAuditFinding(BaseModel):
    severity: str
    category: str
    message: str
    evidence: Optional[str] = None


class BlogContentAuditGapItem(BaseModel):
    topic: str
    status: Literal["covered", "partial", "missing"]
    evidence: Optional[str] = None


class BlogContentAuditListItem(BaseModel):
    id: int
    run_id: int
    wp_post_id: Optional[int] = None
    url: str
    title: Optional[str] = None
    slug: Optional[str] = None
    article_type: str
    word_count: int
    word_target: int
    word_target_met: bool
    overall_score: Optional[float] = None
    top_findings: list[BlogContentAuditFinding] = Field(default_factory=list)

    class Config:
        from_attributes = True


class BlogContentAuditListResponse(BaseModel):
    items: list[BlogContentAuditListItem]
    total: int
    page: int
    page_size: int


class BlogContentAuditDetailResponse(BaseModel):
    id: int
    run_id: int
    wp_post_id: Optional[int] = None
    url: str
    title: Optional[str] = None
    slug: Optional[str] = None
    article_type: str
    word_count: int
    word_target: int
    word_target_met: bool
    seo_score: Optional[float] = None
    content_quality_score: Optional[float] = None
    search_intent_score: Optional[float] = None
    content_depth_score: Optional[float] = None
    originality_score: Optional[float] = None
    eeat_score: Optional[float] = None
    adsense_quality_score: Optional[float] = None
    overall_score: Optional[float] = None
    findings: list[BlogContentAuditFinding] = Field(default_factory=list)
    gap_matrix: list[BlogContentAuditGapItem] = Field(default_factory=list)
    adsense_risks: list[dict[str, Any]] = Field(default_factory=list)
    content_hash: Optional[str] = None
    recommended_topics: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


FixStatus = Literal["proposed", "edited", "skipped", "ready"]
FixTargetField = Literal["content", "meta"]


class BlogContentAuditFixGenerateRequest(BaseModel):
    force: bool = False


class BlogContentAuditFixEditRequest(BaseModel):
    after_proposed: str = Field(..., min_length=1)


class BlogContentAuditFixPreviewCombinedRequest(BaseModel):
    fix_ids: list[int] = Field(default_factory=list)


class BlogContentAuditFixResponse(BaseModel):
    id: int
    audit_id: int
    fix_type: str
    target_field: str
    title: str
    description: Optional[str] = None
    severity: str
    status: str
    finding_refs: list[dict[str, Any]] = Field(default_factory=list)
    before_text: Optional[str] = None
    after_proposed: Optional[str] = None
    diff_segments: list[dict[str, Any]] = Field(default_factory=list)
    generator_meta: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BlogContentAuditFixListResponse(BaseModel):
    items: list[BlogContentAuditFixResponse]
    total: int


class BlogContentAuditFixPreviewCombinedResponse(BaseModel):
    merged_content: str
    merged_meta: Optional[str] = None
    diff_segments: list[dict[str, Any]] = Field(default_factory=list)
    word_count_before: int = 0
    word_count_after: int = 0
