from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://user:password@localhost/seo_automation"
    
    # Authentication
    secret_key: str = "your-secret-key-here"
    jwt_algorithm: str = "HS256"
    jwt_expiration: int = 3600
    
    # External APIs
    serpapi_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    # Legacy: no longer used; all LLM calls go through Groq. Kept so existing .env files load cleanly.
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "stepfun/step-3.5-flash:free"
    openrouter_humanize_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    groq_api_key: Optional[str] = None
    # Groq key for trending keywords fallback when SerpAPI returns 429/rate limit (optional; falls back to groq_api_key)
    trending_fallback_groq_api_key: Optional[str] = None
    # Apify API key (YouTube scraper, AI humanizer, etc.)
    apify_api_key: Optional[str] = None
    apify_humanizer_actor_id: str = "akash9078~ai-content-humanizer"
    # Seconds to wait between blog-generation LLM calls to avoid Groq TPM rate limit (default 4)
    groq_blog_delay_seconds: float = 4.0
    # TLS verify for outbound httpx (Groq, Tavily, SerpAPI, etc.). Uses OS trust store via truststore, then certifi. Set false only for local dev.
    groq_ssl_verify: bool = True
    
    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

