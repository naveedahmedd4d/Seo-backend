from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

ActionType = Literal[
    "low_ctr",
    "striking_distance",
    "content_decay",
    "zero_clicks",
    "keyword_gap",
    "cannibalization",
    "not_indexed",
]

ActionStatus = Literal["pending_review", "approved", "dismissed", "snoozed"]
ActionSeverity = Literal["critical", "warning", "info"]


class GscOAuthStatusResponse(BaseModel):
    connected: bool
    account_email: Optional[str] = None


class GscOAuthSetupInfoResponse(BaseModel):
    redirect_uri: str
    has_app_credentials: bool
    client_id: Optional[str] = None
    developer_portal_url: str = "https://console.cloud.google.com/apis/credentials"
    platform_available: bool = False


class GscOAuthConnectRequest(BaseModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    flow: Literal["popup", "redirect"] = "redirect"


class GscOAuthSetupResponse(BaseModel):
    authorization_url: str


class GscPropertyCreate(BaseModel):
    site_url: str
    display_name: Optional[str] = None
    wordpress_site_id: Optional[int] = None


class GscPropertyResponse(BaseModel):
    id: int
    site_url: str
    display_name: Optional[str] = None
    wordpress_site_id: Optional[int] = None
    last_synced_at: Optional[str] = None
    is_saved: bool = False


class GscPropertiesListResponse(BaseModel):
    available: list[GscPropertyResponse]
    saved: list[GscPropertyResponse]
    fetch_error: Optional[str] = None


class GscSyncRequest(BaseModel):
    property_id: Optional[int] = None


class GscSyncResult(BaseModel):
    property_id: int
    page_rows_synced: Optional[int] = None
    query_rows_synced: Optional[int] = None
    actions_generated: Optional[int] = None
    data_through: Optional[str] = None
    last_synced_at: Optional[str] = None
    error: Optional[str] = None


class GscSyncResponse(BaseModel):
    results: list[GscSyncResult]


class SummaryChanges(BaseModel):
    clicks_pct: float = 0.0
    impressions_pct: float = 0.0
    ctr_pp: float = 0.0
    position_delta: float = 0.0


class OverviewSummary(BaseModel):
    clicks: int = 0
    impressions: int = 0
    ctr: float = 0.0
    position: float = 0.0
    changes: SummaryChanges


class TrendPoint(BaseModel):
    week: str
    clicks: int


class TopPageItem(BaseModel):
    page: str
    clicks: int
    position: float


class GscOverviewResponse(BaseModel):
    summary: OverviewSummary
    trend: list[TrendPoint]
    issue_breakdown: dict[str, int]
    top_pages: list[TopPageItem]
    open_action_count: int = 0
    last_synced_at: Optional[str] = None
    data_through: Optional[str] = None


class ActionItemResponse(BaseModel):
    id: int
    action_type: str
    severity: str
    page: Optional[str] = None
    query: Optional[str] = None
    metric_snapshot: Optional[dict[str, Any]] = None
    suggestion: Optional[str] = None
    impact_score: float = 0.0
    status: str
    linked_published_post_id: Optional[int] = None
    linked_workflow_id: Optional[int] = None
    snoozed_until: Optional[str] = None
    detected_at: Optional[str] = None
    fix_status: Optional[str] = None
    fix_result: Optional[dict[str, Any]] = None
    indexing_status: Optional[str] = None
    gsc_inspect_url: Optional[str] = None
    fixed_at: Optional[str] = None


class ActionListResponse(BaseModel):
    items: list[ActionItemResponse]
    total: int
    open_count: int


class ActionUpdateRequest(BaseModel):
    status: ActionStatus
    snoozed_until: Optional[datetime] = None


class ActionBulkRequest(BaseModel):
    ids: list[int]
    status: Literal["approved", "dismissed"]
    snoozed_until: Optional[datetime] = None


class GscIndexAuditStartRequest(BaseModel):
    property_id: int
    force: bool = False


class GscIndexAuditStatusResponse(BaseModel):
    id: Optional[int] = None
    property_id: int
    status: str = "idle"
    total_urls: int = 0
    processed_urls: int = 0
    not_indexed_count: int = 0
    error_message: Optional[str] = None
    warning_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class IndexingQueueResponse(BaseModel):
    items: list[ActionItemResponse]
    total: int
