from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class BlogSlotInput(BaseModel):
    title: str
    focus_keyword: Optional[str] = None
    article_type: Optional[str] = None


class AuthoritySiteCreate(BaseModel):
    wordpress_site_id: int
    money_page_title: str
    money_page_focus_keyword: str
    money_page_slug: Optional[str] = None


class MoneyPageUpdate(BaseModel):
    title: Optional[str] = None
    slug: Optional[str] = None
    focus_keyword: Optional[str] = None
    status: Optional[Literal["planned", "draft", "published"]] = None


class ClusterCreate(BaseModel):
    title: str
    focus_keyword: str
    slug: Optional[str] = None
    blogs: Optional[List[BlogSlotInput]] = None


class ClusterUpdate(BaseModel):
    title: Optional[str] = None
    slug: Optional[str] = None
    focus_keyword: Optional[str] = None
    status: Optional[Literal["planned", "draft", "published"]] = None


class BlogCreate(BaseModel):
    title: str
    focus_keyword: str
    slug: Optional[str] = None
    article_type: Optional[str] = None


class BlogUpdate(BaseModel):
    title: Optional[str] = None
    slug: Optional[str] = None
    focus_keyword: Optional[str] = None
    status: Optional[Literal["planned", "draft", "published"]] = None
    article_type: Optional[str] = None


class GenerateRequest(BaseModel):
    include_research: bool = False


class ContentUpdate(BaseModel):
    body: str
    meta_description: Optional[str] = None
    seo_title: Optional[str] = None


class PublishRequest(BaseModel):
    status: Literal["draft", "publish"] = "draft"


class BlogNodeResponse(BaseModel):
    id: int
    title: str
    slug: str
    focus_keyword: str
    status: str
    article_type: Optional[str] = None
    link_count: int = 0
    body: Optional[str] = None
    meta_description: Optional[str] = None
    seo_title: Optional[str] = None
    wp_link: Optional[str] = None
    generation_error: Optional[str] = None
    is_generating: bool = False
    published_id: Optional[int] = None


class ServiceHubResponse(BaseModel):
    id: int
    title: str
    slug: str
    focus_keyword: str
    status: str
    color: str
    sort_order: int
    blog_count: int
    blogs: List[BlogNodeResponse] = Field(default_factory=list)
    body: Optional[str] = None
    meta_description: Optional[str] = None
    seo_title: Optional[str] = None
    wp_link: Optional[str] = None
    generation_error: Optional[str] = None
    is_generating: bool = False
    published_id: Optional[int] = None


class MoneyPageResponse(BaseModel):
    title: str
    slug: str
    focus_keyword: str
    status: str
    inbound_service_count: int = 0
    body: Optional[str] = None
    meta_description: Optional[str] = None
    seo_title: Optional[str] = None
    wp_link: Optional[str] = None
    generation_error: Optional[str] = None
    is_generating: bool = False
    published_id: Optional[int] = None


class AuthorityStatsResponse(BaseModel):
    money_page_count: int = 1
    money_page_live: int = 0
    service_hubs_total: int = 0
    service_hubs_live: int = 0
    service_hubs_draft: int = 0
    blog_posts_total: int = 0
    blog_posts_published: int = 0
    blog_posts_draft: int = 0
    internal_links_total: int = 0
    internal_links_this_week: int = 0


class LinkIssueResponse(BaseModel):
    id: str
    severity: Literal["warning", "error", "success"]
    message: str
    cluster_id: Optional[int] = None
    fixable: bool = False


class LinkHealthResponse(BaseModel):
    blog_to_service_pct: int = 0
    service_to_money_pct: int = 0
    sibling_links_pct: int = 0
    issues: List[LinkIssueResponse] = Field(default_factory=list)


class AuthoritySiteResponse(BaseModel):
    id: int
    name: str
    domain: str
    wordpress_site_id: int
    money_page: MoneyPageResponse
    service_hubs: List[ServiceHubResponse] = Field(default_factory=list)
    stats: AuthorityStatsResponse
    link_health: LinkHealthResponse


class AuthoritySiteSummaryResponse(BaseModel):
    id: int
    name: str
    domain: str
    wordpress_site_id: int
    money_page_title: str
    stats: AuthorityStatsResponse
