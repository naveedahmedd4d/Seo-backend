from pydantic import BaseModel, field_validator
from typing import List, Literal, Optional


class KeywordCoverageInfo(BaseModel):
    status: Literal["existing", "new"]
    matched_url: Optional[str] = None
    matched_title: Optional[str] = None
    match_method: Optional[str] = None
    compared_at: Optional[str] = None


class GithubRepoInfo(BaseModel):
    description: Optional[str] = None
    url: Optional[str] = None
    language: Optional[str] = None
    stars_today: Optional[int] = None
    forks: Optional[int] = None
    topics: Optional[List[str]] = None
    suggested_titles: Optional[List[str]] = None
    suggested_angles: Optional[List[str]] = None
    suggested_keywords: Optional[List[str]] = None


class TrendingKeywordResponse(BaseModel):
    id: Optional[int] = None
    keyword: str
    value: Optional[int] = None
    increase_percentage: Optional[int] = None
    timestamp: Optional[str] = None
    fetched_at: Optional[str] = None
    category: Optional[str] = None
    kd: Optional[int] = None
    bio_blog_workflow_id: Optional[int] = None
    net_worth_blog_workflow_id: Optional[int] = None
    blog_workflow_id: Optional[int] = None
    source: Optional[str] = None
    parent_id: Optional[int] = None
    subs: Optional[List["TrendingKeywordResponse"]] = None
    coverage: Optional[KeywordCoverageInfo] = None
    repo: Optional[GithubRepoInfo] = None


class SetBlogWorkflowRequest(BaseModel):
    workflow_id: int
    content_type: Literal[
        "biography", "net_worth", "cluster_hub",
        "news_timeline", "news_explainer", "news_opinion",
    ]


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
    source: Literal["trending", "manual", "cluster", "reddit"] = "trending"
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
    source: Literal["trending", "manual", "cluster", "github", "reddit"] = "trending"


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


class GenerateGithubIdeasRequest(BaseModel):
    keyword_ids: List[int]


class GenerateGithubIdeasResponse(BaseModel):
    processed: int
    failed: int
    message: str
    keywords: List[TrendingKeywordResponse] = []


TrendingKeywordResponse.model_rebuild()
