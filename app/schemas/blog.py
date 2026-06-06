from pydantic import BaseModel, field_validator
from typing import List, Optional, Dict, Any, Union, Literal

CelebrityContentType = Literal["biography", "religious", "net_worth"]


def _normalize_related_queries(v: List[Any]) -> List[Dict[str, str]]:
    """Accept list of strings (legacy) or list of {query, answer}; return list of dicts."""
    if not v:
        return []
    out: List[Dict[str, str]] = []
    for x in v[:12]:
        if isinstance(x, dict):
            out.append({"query": str(x.get("query", "")).strip(), "answer": str(x.get("answer", "")).strip()})
        else:
            out.append({"query": str(x).strip(), "answer": ""})
    return out


class BlogGenerateRequest(BaseModel):
    topic: str
    description: Optional[str] = None  # Context/background information
    keywords: Optional[List[str]] = None
    category: str = "Technology & AI"  # Replaces tone - controls writing style
    include_research: bool = True  # Run Tavily research before generating content
    celebrity_content_type: Optional[CelebrityContentType] = None  # Celebrities only: generate one article type
    suggested_title: Optional[str] = None  # Pre-set SEO title (e.g. news-hook from research)
    suggested_meta_description: Optional[str] = None
    news_context: Optional[str] = None  # Pre-formatted news sources block for citations

    @field_validator("celebrity_content_type")
    @classmethod
    def validate_celebrity_content_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        allowed = {"biography", "religious", "net_worth"}
        if v not in allowed:
            raise ValueError(f"celebrity_content_type must be one of: {', '.join(sorted(allowed))}")
        return v


class BlogGenerateResponse(BaseModel):
    workflow_id: int
    status: str
    message: str


class BlogWorkflowStatus(BaseModel):
    id: int
    status: str
    state_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: str
    updated_at: str


class BlogUpdateRequest(BaseModel):
    """Request body for updating/saving edited blog content."""
    title: str
    meta_description: str
    body_content: str
    faqs: List[Dict[str, str]]
    related_queries: List[Dict[str, str]]  # [{query, answer}]; API accepts legacy list of strings and normalizes
    slug: Optional[str] = None  # URL slug; omit to leave existing value unchanged

    @field_validator("related_queries", mode="before")
    @classmethod
    def normalize_related_queries(cls, v: Any) -> List[Dict[str, str]]:
        if v is None:
            return []
        return _normalize_related_queries(v if isinstance(v, list) else [])
    blog_key: Optional[str] = None  # "parent", "child_0", "child_1" for tree workflows
    featured_image: Optional[Dict[str, str]] = None  # url, thumbnail, title, alt (SEO)


class BlogWorkflowListItem(BaseModel):
    id: int
    status: str
    created_at: str
    topic: Optional[str] = None
    title: Optional[str] = None
    is_tree: Optional[bool] = None
    religion_skipped: Optional[bool] = None  # celebrity tree: only parent + net worth


class BlogWorkflowListResponse(BaseModel):
    workflows: List[BlogWorkflowListItem]


class BlogPublishRequest(BaseModel):
    workflow_id: int
    wordpress_site_id: int
    status: str = "draft"
    blog_key: Optional[str] = None  # "parent", "child_0", "child_1" for tree; default parent
    body_content_html: Optional[str] = None  # when set, use as post body (same as preview); backend appends related + schema


class BlogPublishResponse(BaseModel):
    success: bool
    message: str
    post_id: Optional[int] = None
    link: Optional[str] = None


class BlogUnpublishRequest(BaseModel):
    published_post_id: int  # id from published_posts table


class BlogUnpublishResponse(BaseModel):
    success: bool
    message: str


class PublishedPostItem(BaseModel):
    """One published article record."""
    id: int
    blog_key: str
    wordpress_site_id: int
    site_name: Optional[str] = None
    wp_post_id: int
    wp_link: Optional[str] = None
    wp_status: str
    title: Optional[str] = None
    published_at: str


class WorkflowPublishStatus(BaseModel):
    """Publish status for all articles in a workflow."""
    workflow_id: int
    posts: List[PublishedPostItem]

