from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Literal


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


class AutocompleteSuggestion(BaseModel):
    value: str
    relevance: Optional[int] = None
    type: Optional[str] = None


class RelatedQueryItem(BaseModel):
    query: str
    value: Optional[str] = None
    extracted_value: Optional[float] = None
    link: Optional[str] = None
    type: Optional[str] = None


class TopicalMappingResponse(BaseModel):
    query: str
    autocomplete_suggestions: List[AutocompleteSuggestion]
    related_queries: List[RelatedQueryItem]
    errors: Optional[List[str]] = None


class SummaryCompetitorInput(BaseModel):
    domain: str
    traffic_share: Optional[int] = None
    overlapping_keywords: Optional[int] = None


class SummaryOpportunityInput(BaseModel):
    keyword: str
    category: Optional[str] = None
    difficulty: Optional[str] = None


class ResearchSummaryRequest(BaseModel):
    query: str
    results: Dict[str, Any]
    topical_mapping: Optional[TopicalMappingResponse] = None
    competitors: Optional[List[SummaryCompetitorInput]] = None
    opportunities: Optional[List[SummaryOpportunityInput]] = None
    category: Optional[str] = None
    celebrity_content_type: Optional[Literal["biography", "religious", "net_worth"]] = None
    content_intent: Optional[Literal["cluster_hub", "news_timeline", "news_explainer", "news_opinion"]] = None
    cluster_subs: Optional[List[str]] = None


class HeadingNode(BaseModel):
    level: Literal[1, 2, 3]
    text: str
    children: List["HeadingNode"] = Field(default_factory=list)


class NewsSourceItem(BaseModel):
    title: str = ""
    link: str = ""
    source: str = ""
    snippet: str = ""
    date: str = ""


HeadingNode.model_rebuild()


class ResearchSummaryResponse(BaseModel):
    summary: str
    h1: str
    outline: List[HeadingNode] = Field(default_factory=list)
    key_topics: List[str] = Field(default_factory=list)
    subtopics: List[str] = Field(default_factory=list)
    core_content_recommendations: List[str] = Field(default_factory=list)
    seo_suggestions: List[str] = Field(default_factory=list)
    topical_coverage: List[str] = Field(default_factory=list)
    target_keywords: List[str] = Field(default_factory=list)
    section_notes: Optional[Dict[str, List[str]]] = None
    category: Optional[str] = None
    celebrity_content_type: Optional[str] = None
    outline_template: Optional[str] = None
    news_headline: Optional[str] = None
    news_hook_paragraph: Optional[str] = None
    news_meta_description: Optional[str] = None
    news_hashtags: Optional[List[str]] = None
    news_sources: Optional[List[NewsSourceItem]] = None
    news_context: Optional[str] = None

