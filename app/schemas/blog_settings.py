from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


ArticleType = Literal[
    "biography",
    "athlete_biography",
    "religion",
    "net_worth",
    "cluster_hub",
    "news_timeline",
    "news_explainer",
    "news_opinion",
    "generic",
]

IntroLength = Literal["short", "standard", "long"]
Creativity = Literal["factual", "balanced", "expressive"]

ARTICLE_TYPES: List[str] = [
    "biography",
    "athlete_biography",
    "religion",
    "net_worth",
    "cluster_hub",
    "news_timeline",
    "news_explainer",
    "news_opinion",
    "generic",
]


class OutlineSectionDict(BaseModel):
    slug: str
    level: Literal[2, 3] = 2
    heading: str
    role: str = "celebrity_focus"
    word_budget: List[int] = Field(default_factory=lambda: [100, 150])
    llm_brief: str = ""
    include_image: bool = False
    kind: Optional[str] = None
    children: List["OutlineSectionDict"] = Field(default_factory=list)

    @field_validator("word_budget")
    @classmethod
    def validate_budget(cls, v: List[int]) -> List[int]:
        if len(v) != 2:
            raise ValueError("word_budget must be [min, max]")
        lo, hi = int(v[0]), int(v[1])
        if lo < 30 or hi > 2000 or lo > hi:
            raise ValueError("word_budget values must be 30-2000 with min <= max")
        return [lo, hi]


OutlineSectionDict.model_rebuild()


class TemplateFormat(BaseModel):
    paragraph_sentences: str = "2-4"
    bullets_per_section: int = 4
    include_info_table: bool = True


class BlogSettingsResponse(BaseModel):
    target_word_count: Optional[int] = None
    intro_length: IntroLength = "standard"
    conclusion_length: IntroLength = "standard"
    faq_count: int = 5
    related_query_count: int = 8
    include_site_cta: bool = True
    cta_text: Optional[str] = None
    cta_href: Optional[str] = None
    auto_featured_image: bool = True
    max_section_images: int = 3
    creativity: Creativity = "balanced"
    custom_style_notes: Optional[str] = None
    seo_power_words: bool = True
    last_updated_footer: bool = True
    updated_at: Optional[str] = None


class BlogSettingsUpdateRequest(BaseModel):
    target_word_count: Optional[int] = None
    intro_length: Optional[IntroLength] = None
    conclusion_length: Optional[IntroLength] = None
    faq_count: Optional[int] = None
    related_query_count: Optional[int] = None
    include_site_cta: Optional[bool] = None
    cta_text: Optional[str] = None
    cta_href: Optional[str] = None
    auto_featured_image: Optional[bool] = None
    max_section_images: Optional[int] = None
    creativity: Optional[Creativity] = None
    custom_style_notes: Optional[str] = None
    seo_power_words: Optional[bool] = None
    last_updated_footer: Optional[bool] = None


class BlogTemplateResponse(BaseModel):
    article_type: str
    name: str
    is_default: bool = True
    is_system: bool = False
    target_word_count: int
    sections: List[Dict[str, Any]]
    format: Dict[str, Any] = Field(default_factory=dict)


class BlogTemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    target_word_count: Optional[int] = None
    sections: List[OutlineSectionDict]
    format: Optional[TemplateFormat] = None


class BlogTemplateListResponse(BaseModel):
    templates: List[BlogTemplateResponse]


@dataclass
class BlogGenerationConfig:
    article_type: str
    target_word_count: int
    min_words: int
    max_tokens: int
    temperature: float
    faq_count: int
    related_query_count: int
    include_site_cta: bool
    cta_text: str
    cta_href: str
    auto_featured_image: bool
    max_section_images: int
    creativity: str
    custom_style_notes: str
    seo_power_words: bool
    last_updated_footer: bool
    intro_length: str
    conclusion_length: str
    sections: List[Dict[str, Any]] = field(default_factory=list)
    format: Dict[str, Any] = field(default_factory=dict)
    template_name: str = ""

    def to_snapshot(self) -> Dict[str, Any]:
        return {
            "article_type": self.article_type,
            "target_word_count": self.target_word_count,
            "min_words": self.min_words,
            "faq_count": self.faq_count,
            "related_query_count": self.related_query_count,
            "include_site_cta": self.include_site_cta,
            "cta_href": self.cta_href,
            "creativity": self.creativity,
            "seo_power_words": self.seo_power_words,
            "last_updated_footer": self.last_updated_footer,
            "max_section_images": self.max_section_images,
            "auto_featured_image": self.auto_featured_image,
        }
