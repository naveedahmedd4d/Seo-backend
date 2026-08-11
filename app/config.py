from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

# Always load backend/.env regardless of process working directory (e.g. uvicorn from repo root).
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://user:password@localhost/seo_automation"
    
    # Authentication
    secret_key: str = "your-secret-key-here"
    jwt_algorithm: str = "HS256"
    jwt_expiration: int = 3600
    # Only this account may use server .env API keys as platform defaults
    platform_owner_email: Optional[str] = None

    # External APIs
    serpapi_key: Optional[str] = None
    mangools_api_token: Optional[str] = None
    tavily_api_key: Optional[str] = None
    # OpenRouter: used exclusively for blog generation when configured.
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "stepfun/step-3.5-flash:free"
    openrouter_humanize_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    # Model used for blog generation via OpenRouter (only active when openrouter_api_key is set).
    blog_openrouter_model: str = "openai/gpt-4o-mini"
    groq_api_key: Optional[str] = None
    # Groq key for trending keywords fallback when SerpAPI returns 429/rate limit (optional; falls back to groq_api_key)
    trending_fallback_groq_api_key: Optional[str] = None
    # Groq key when blog generation hits 429/TPD on primary key (optional; falls back to trending_fallback_groq_api_key)
    blog_fallback_groq_api_key: Optional[str] = None
    # Apify API key (YouTube scraper, AI humanizer, etc.)
    apify_api_key: Optional[str] = None
    apify_humanizer_actor_id: str = "akash9078~ai-content-humanizer"
    # Seconds to wait between blog-generation LLM calls to avoid Groq TPM rate limit (default 4)
    groq_blog_delay_seconds: float = 4.0
    # TLS verify for outbound httpx (Groq, Tavily, SerpAPI, etc.). Uses OS trust store via truststore, then certifi. Set false only for local dev.
    groq_ssl_verify: bool = True
    
    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # Public API URL (used for social post image URLs — must be reachable by external platforms)
    api_public_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"

    # Pinterest OAuth
    pinterest_client_id: Optional[str] = None
    pinterest_client_secret: Optional[str] = None
    pinterest_redirect_uri: Optional[str] = None

    # X / Mastodon / Blogger OAuth redirect overrides (optional)
    x_redirect_uri: Optional[str] = None
    mastodon_redirect_uri: Optional[str] = None
    blogger_redirect_uri: Optional[str] = None
    reddit_redirect_uri: Optional[str] = None
    linkedin_redirect_uri: Optional[str] = None
    facebook_redirect_uri: Optional[str] = None
    instagram_redirect_uri: Optional[str] = None
    reddit_user_agent: Optional[str] = None

    # Optional server-wide social app creds (unused — users enter per-account creds in UI).
    # Declared so placeholder values in .env do not break startup.
    linkedin_client_id: Optional[str] = None
    linkedin_client_secret: Optional[str] = None
    meta_app_id: Optional[str] = None
    meta_app_secret: Optional[str] = None

    # Google Search Console OAuth
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_gsc_redirect_uri: Optional[str] = None
    
    model_config = {
        "env_file": str(_ENV_FILE),
        "case_sensitive": False,
        "extra": "ignore",
    }


settings = Settings()

