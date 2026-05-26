from pydantic import BaseModel
from typing import List, Dict, Any, Optional


class KeywordResearchRequest(BaseModel):
    query: str


class KeywordResearchResponse(BaseModel):
    query: str
    results: Dict[str, Any]
    created_at: str


class ContentSuggestionRequest(BaseModel):
    query: str
    num_suggestions: int = 5


class ContentSuggestionResponse(BaseModel):
    suggestions: List[Dict[str, Any]]


class ResearchHistoryResponse(BaseModel):
    id: int
    query: str
    results: Optional[Dict[str, Any]] = None
    created_at: str

    class Config:
        from_attributes = True

