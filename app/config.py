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
    # Platform admin allowed to manage shared OAuth integrations.
    platform_owner_email: Optional[str] = None

    # External APIs
    serpapi_key: Optional[str] = None
    mangools_api_token: Optional[str] = None
    tavily_api_key: Optional[str] = None
    # OpenRouter: used exclusively for blog generation when configured.
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "openrouter/free"
    openrouter_humanize_model: str = "google/gemma-4-31b-it:free"
    # Model used for blog generation via OpenRouter (only active when openrouter_api_key is set).
    blog_openrouter_model: str = "openrouter/free"
    # Google AI Studio / Gemini Developer API (free-tier Flash models).
    gemini_api_key: Optional[str] = None
    blog_gemini_model: str = "gemini-3.8-flash"
    # Cloudflare Workers AI (account id + API token).
    cloudflare_api_token: Optional[str] = None
    cloudflare_account_id: Optional[str] = None
    blog_cloudflare_model: str = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    # Mistral Free mode
    mistral_api_key: Optional[str] = None
    blog_mistral_model: str = "mistral-small-latest"
    # Cohere trial
    cohere_api_key: Optional[str] = None
    blog_cohere_model: str = "command-r-plus-08-2024"
    # NVIDIA NIM / Build
    nvidia_api_key: Optional[str] = None
    blog_nvidia_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    groq_api_key: Optional[str] = None
    # Groq key for trending keywords fallback when SerpAPI returns 429/rate limit (optional; falls back to groq_api_key)
    trending_fallback_groq_api_key: Optional[str] = None
    # Groq key when blog generation hits 429/TPD on primary key (optional; falls back to trending_fallback_groq_api_key)
    blog_fallback_groq_api_key: Optional[str] = None
    # Apify API key (YouTube scraper, AI humanizer, etc.)
    apify_api_key: Optional[str] = None
    apify_humanizer_actor_id: str = "akash9078~ai-content-humanizer"
    apify_reddit_actor_id: str = "fatihtahta~reddit-scraper-search-fast"
    reddit_trends_run_timeout_seconds: int = 600
    reddit_trends_poll_interval_seconds: float = 5.0
    # Seconds to wait between blog-generation LLM calls to avoid Groq TPM rate limit (default 4)
    groq_blog_delay_seconds: float = 4.0
    # TLS verify for outbound httpx (Groq, Tavily, SerpAPI, etc.). Uses OS trust store via truststore, then certifi. Set false only for local dev.
    groq_ssl_verify: bool = True
    
    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # Public API URL (used for social post image URLs — must be reachable by external platforms)
    api_public_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"

    # Shared secret for the backend to call the Next.js infographic-rendering route
    # (frontend_url + /api/infographic/render). Must match INFOGRAPHIC_RENDER_SECRET on Vercel.
    infographic_render_secret: Optional[str] = None
    # Branding for the 16:9 infographic frame. A site name adds a footer; a logo URL
    # (https) adds a logo next to it.
    infographic_brand_color: str = "#2563eb"
    infographic_brand_name: Optional[str] = None
    infographic_brand_logo_url: Optional[str] = None

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

    # Server-wide social app credentials. Meta powers the customer-facing,
    # one-click Facebook OAuth flow and is never exposed to the browser.
    linkedin_client_id: Optional[str] = None
    linkedin_client_secret: Optional[str] = None
    meta_app_id: Optional[str] = None
    meta_app_secret: Optional[str] = None
    # Instagram API with Instagram Login. These may differ from the Facebook
    # Login credentials shown for the same Meta app.
    instagram_app_id: Optional[str] = None
    instagram_app_secret: Optional[str] = None

    # Google Search Console OAuth
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_gsc_redirect_uri: Optional[str] = None
    google_ga_redirect_uri: Optional[str] = None

    # Upload-Post unified social publishing (https://docs.upload-post.com)
    # When set, supported platforms publish via Upload-Post instead of first-party OAuth.
    upload_post_api_key: Optional[str] = None
    upload_post_user: Optional[str] = None
    upload_post_base_url: str = "https://api.upload-post.com/api"
    # Optional Facebook Page id when posting via Upload-Post (falls back to upload_post_user if numeric).
    upload_post_facebook_page_id: Optional[str] = None
    
    model_config = {
        "env_file": str(_ENV_FILE),
        "case_sensitive": False,
        "extra": "ignore",
    }


settings = Settings()
