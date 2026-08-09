from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


PreferredProvider = Literal["default", "openrouter", "groq"]
ApiKeyProvider = Literal[
    "openrouter",
    "groq",
    "trending_fallback_groq",
    "blog_fallback_groq",
    "serpapi",
    "tavily",
    "mangools",
    "apify",
]


class LlmModelOption(BaseModel):
    id: str
    label: str
    provider: Literal["openrouter", "groq", "default"]


class LlmSettingsResponse(BaseModel):
    is_platform_owner: bool = False

    openrouter_configured: bool = False
    openrouter_hint: Optional[str] = None
    openrouter_platform_available: bool = False

    groq_configured: bool = False
    groq_hint: Optional[str] = None
    groq_platform_available: bool = False

    trending_fallback_groq_configured: bool = False
    trending_fallback_groq_hint: Optional[str] = None
    trending_fallback_groq_platform_available: bool = False

    blog_fallback_groq_configured: bool = False
    blog_fallback_groq_hint: Optional[str] = None
    blog_fallback_groq_platform_available: bool = False

    serpapi_configured: bool = False
    serpapi_hint: Optional[str] = None
    serpapi_platform_available: bool = False

    tavily_configured: bool = False
    tavily_hint: Optional[str] = None
    tavily_platform_available: bool = False

    mangools_configured: bool = False
    mangools_hint: Optional[str] = None
    mangools_platform_available: bool = False

    apify_configured: bool = False
    apify_hint: Optional[str] = None
    apify_platform_available: bool = False

    preferred_provider: PreferredProvider = "default"
    preferred_model: Optional[str] = None
    updated_at: Optional[datetime] = None


class LlmSettingsUpdateRequest(BaseModel):
    openrouter_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    trending_fallback_groq_api_key: Optional[str] = None
    blog_fallback_groq_api_key: Optional[str] = None
    serpapi_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    mangools_api_token: Optional[str] = None
    apify_api_key: Optional[str] = None

    clear_openrouter_api_key: bool = False
    clear_groq_api_key: bool = False
    clear_trending_fallback_groq_api_key: bool = False
    clear_blog_fallback_groq_api_key: bool = False
    clear_serpapi_api_key: bool = False
    clear_tavily_api_key: bool = False
    clear_mangools_api_token: bool = False
    clear_apify_api_key: bool = False

    preferred_provider: Optional[PreferredProvider] = None
    preferred_model: Optional[str] = Field(
        default=None,
        description="Allowlisted model id, or null/'default' for platform default",
    )


class LlmModelsResponse(BaseModel):
    models: List[LlmModelOption]


class LlmTestRequest(BaseModel):
    provider: Literal["openrouter", "groq"]
    api_key: Optional[str] = Field(
        default=None,
        description="Key to test; omit to test the saved key for this provider",
    )


class LlmTestResponse(BaseModel):
    ok: bool
    message: str
