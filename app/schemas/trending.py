from pydantic import BaseModel, field_validator
from typing import List, Literal, Optional


class TrendingKeywordResponse(BaseModel):
    id: Optional[int] = None
    keyword: str
    value: Optional[int] = None
    timestamp: Optional[str] = None
    fetched_at: Optional[str] = None
    category: Optional[str] = None
    bio_blog_workflow_id: Optional[int] = None
    net_worth_blog_workflow_id: Optional[int] = None
    source: Optional[str] = None


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


class TrendingKeywordsResponse(BaseModel):
    keywords: List[TrendingKeywordResponse]
    count: int
