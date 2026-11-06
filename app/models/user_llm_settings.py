from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
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
    groq_api_key = Column(Text, nullable=True)
    trending_fallback_groq_api_key = Column(Text, nullable=True)
    blog_fallback_groq_api_key = Column(Text, nullable=True)
    serpapi_api_key = Column(Text, nullable=True)
    tavily_api_key = Column(Text, nullable=True)
    mangools_api_token = Column(Text, nullable=True)
    apify_api_key = Column(Text, nullable=True)
    preferred_provider = Column(String, nullable=False, default="default", server_default="default")
    preferred_model = Column(String, nullable=True)
    # Manual fallback lineup, e.g. ["gemini","openrouter","groq","cloudflare","mistral","cohere","nvidia"]
    fallback_order = Column(JSON, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="llm_settings")
