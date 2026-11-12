from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class UserLlmSettings(Base):
    """Per-user API keys (LLM + research) and blog model preferences."""

    __tablename__ = "user_llm_settings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    openrouter_api_key = Column(Text, nullable=True)  # Fernet-encrypted
    gemini_api_key = Column(Text, nullable=True)
    cloudflare_api_token = Column(Text, nullable=True)
    cloudflare_account_id = Column(Text, nullable=True)  # not secret; plain text OK
    mistral_api_key = Column(Text, nullable=True)
    cohere_api_key = Column(Text, nullable=True)
    nvidia_api_key = Column(Text, nullable=True)
    # Claude Code routine (fire URL + token + callback secret)
    claude_routine_url = Column(Text, nullable=True)
    claude_routine_token = Column(Text, nullable=True)
    claude_routine_callback_secret = Column(Text, nullable=True)
    # Claude LLM relay (async /api/v1/run + poll /api/v1/jobs/:id)
    claude_llm_api_key = Column(Text, nullable=True)
    claude_llm_base_url = Column(Text, nullable=True)
    claude_llm_webhook_secret = Column(Text, nullable=True)
    claude_llm_timeout_ms = Column(Integer, nullable=True)
    groq_api_key = Column(Text, nullable=True)
    trending_fallback_groq_api_key = Column(Text, nullable=True)
    blog_fallback_groq_api_key = Column(Text, nullable=True)
    serpapi_api_key = Column(Text, nullable=True)
    # Custom Image Search (Blog Booster-compatible) for social post images
    custom_image_search_api_key = Column(Text, nullable=True)
    custom_image_search_base_url = Column(Text, nullable=True)
    tavily_api_key = Column(Text, nullable=True)
    mangools_api_token = Column(Text, nullable=True)
    apify_api_key = Column(Text, nullable=True)
    preferred_provider = Column(String, nullable=False, default="default", server_default="default")
    preferred_model = Column(String, nullable=True)
    # Manual fallback lineup, e.g. ["gemini","openrouter","groq","cloudflare","mistral","cohere","nvidia"]
    fallback_order = Column(JSON, nullable=True)
    # Enabled free news feeds and the last daily topic run (top 10, LLM top 3, one blog).
    news_topic_sources = Column(JSON, nullable=True)
    news_topics = Column(JSON, nullable=True)
    news_daily_blog_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    news_wordpress_site_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="llm_settings")
