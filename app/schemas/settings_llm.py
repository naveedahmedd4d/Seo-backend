from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


LlmProviderId = Literal[
    "gemini",
    "openrouter",
    "groq",
    "cloudflare",
    "mistral",
    "cohere",
    "nvidia",
]
PreferredProvider = Literal[
    "default",
    "gemini",
    "openrouter",
    "groq",
    "cloudflare",
    "mistral",
    "cohere",
    "nvidia",
    "claude_routine",
]
ApiKeyProvider = Literal[
    "gemini",
    "openrouter",
    "groq",
    "cloudflare",
    "mistral",
    "cohere",
    "nvidia",
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
    provider: Literal[
        "gemini",
        "openrouter",
        "groq",
        "cloudflare",
        "mistral",
        "cohere",
        "nvidia",
        "default",
    ]
    free: bool = False


class LlmSettingsResponse(BaseModel):
    is_platform_owner: bool = False

    gemini_configured: bool = False
    gemini_hint: Optional[str] = None
    gemini_platform_available: bool = False

    openrouter_configured: bool = False
    openrouter_hint: Optional[str] = None
    openrouter_platform_available: bool = False

    groq_configured: bool = False
    groq_hint: Optional[str] = None
    groq_platform_available: bool = False

    cloudflare_configured: bool = False
    cloudflare_hint: Optional[str] = None
    cloudflare_platform_available: bool = False
    cloudflare_account_id: Optional[str] = None

    mistral_configured: bool = False
    mistral_hint: Optional[str] = None
    mistral_platform_available: bool = False

    cohere_configured: bool = False
    cohere_hint: Optional[str] = None
    cohere_platform_available: bool = False

    nvidia_configured: bool = False
    nvidia_hint: Optional[str] = None
    nvidia_platform_available: bool = False

    claude_routine_url: Optional[str] = None
    claude_routine_configured: bool = False
    claude_routine_token_configured: bool = False
    claude_routine_token_hint: Optional[str] = None
    claude_routine_callback_secret_configured: bool = False
    claude_routine_callback_secret_hint: Optional[str] = None
    claude_routine_webhook_path: str = "/api/webhooks/claude-routine"

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
    fallback_order: List[LlmProviderId] = Field(default_factory=list)
    updated_at: Optional[datetime] = None


class LlmSettingsUpdateRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    cloudflare_api_token: Optional[str] = None
    cloudflare_account_id: Optional[str] = None
    mistral_api_key: Optional[str] = None
    cohere_api_key: Optional[str] = None
    nvidia_api_key: Optional[str] = None
    claude_routine_url: Optional[str] = None
    claude_routine_token: Optional[str] = None
    claude_routine_callback_secret: Optional[str] = None
    trending_fallback_groq_api_key: Optional[str] = None
    blog_fallback_groq_api_key: Optional[str] = None
    serpapi_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    mangools_api_token: Optional[str] = None
    apify_api_key: Optional[str] = None

    clear_gemini_api_key: bool = False
    clear_openrouter_api_key: bool = False
    clear_groq_api_key: bool = False
    clear_cloudflare_api_token: bool = False
    clear_mistral_api_key: bool = False
    clear_cohere_api_key: bool = False
    clear_nvidia_api_key: bool = False
    clear_claude_routine: bool = False
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
    fallback_order: Optional[List[LlmProviderId]] = None


class LlmModelsResponse(BaseModel):
    models: List[LlmModelOption]


class LlmTestRequest(BaseModel):
    provider: LlmProviderId
    api_key: Optional[str] = Field(
        default=None,
        description="Key to test; omit to test the saved key for this provider",
    )
    account_id: Optional[str] = Field(
        default=None,
        description="Cloudflare account id (required when testing Cloudflare)",
    )
    model: Optional[str] = Field(
        default=None,
        description=(
            "Exact model id to probe. Must be an allowlisted model belonging to "
            "`provider`. Omit to probe this provider's default model."
        ),
    )


class LlmTestResponse(BaseModel):
    ok: bool
    message: str
