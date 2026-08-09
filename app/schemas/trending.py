from pydantic import BaseModel, field_validator
from typing import List, Literal, Optional


class KeywordCoverageInfo(BaseModel):
    status: Literal["existing", "new"]
    matched_url: Optional[str] = None
    matched_title: Optional[str] = None
    match_method: Optional[str] = None
    compared_at: Optional[str] = None


class TrendingKeywordResponse(BaseModel):
    id: Optional[int] = None
    keyword: str
    value: Optional[int] = None
    timestamp: Optional[str] = None
    fetched_at: Optional[str] = None
    category: Optional[str] = None
    kd: Optional[int] = None
    bio_blog_workflow_id: Optional[int] = None
    net_worth_blog_workflow_id: Optional[int] = None
    source: Optional[str] = None
    parent_id: Optional[int] = None
    subs: Optional[List["TrendingKeywordResponse"]] = None
    coverage: Optional[KeywordCoverageInfo] = None


class SetBlogWorkflowRequest(BaseModel):
    workflow_id: int
    content_type: Literal["biography", "net_worth"]


class AddManualKeywordsRequest(BaseModel):
    keywords: List[str]
    category: Optional[str] = None

    @field_validator("keywords", mode="before")
    @classmethod
    def normalize_keywords(cls, value):
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        return value


class AddClusterSubsRequest(BaseModel):
    keywords: List[str]

    @field_validator("keywords", mode="before")
    @classmethod
    def normalize_keywords(cls, value):
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        return value


class EstimateKdRequest(BaseModel):
    keyword_ids: List[int]
    source: Literal["trending", "manual", "cluster"] = "trending"
    force: bool = False


class MangoolsLimitsResponse(BaseModel):
    remaining: int
    limit: int
    reset_seconds: Optional[int] = None
    short_period_max: Optional[int] = None
    upgraded_account: bool = False
    message: str = ""


class EstimateKdResponse(BaseModel):
    scored: int
    skipped: int
    remaining_before: int
    remaining_after: Optional[int] = None
    limit: Optional[int] = None
    reset_seconds: Optional[int] = None
    message: str
    keywords: List[TrendingKeywordResponse] = []


class SyncCoverageRequest(BaseModel):
    wordpress_site_id: int
    source: Literal["trending", "manual", "cluster"] = "trending"


class SyncCoverageResponse(BaseModel):
    existing: int
    new: int
    total: int
    compared_at: str
    wordpress_site_id: int
    source: str
    message: str = ""


class TrendingKeywordsResponse(BaseModel):
    keywords: List[TrendingKeywordResponse]
    count: int


TrendingKeywordResponse.model_rebuild()
