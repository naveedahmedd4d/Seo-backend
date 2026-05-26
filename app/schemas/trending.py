from pydantic import BaseModel
from typing import List, Dict, Any, Optional


class TrendingKeywordResponse(BaseModel):
    id: Optional[int] = None
    keyword: str
    value: Optional[int] = None
    timestamp: Optional[str] = None
    fetched_at: Optional[str] = None
    category: Optional[str] = None
    blog_workflow_id: Optional[int] = None


class SetBlogWorkflowRequest(BaseModel):
    workflow_id: int


class TrendingKeywordsResponse(BaseModel):
    keywords: List[TrendingKeywordResponse]
    count: int

