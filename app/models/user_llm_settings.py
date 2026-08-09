from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class UserLlmSettings(Base):
    """Per-user API keys (LLM + research) and blog model preferences."""

    __tablename__ = "user_llm_settings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    openrouter_api_key = Column(Text, nullable=True)  # Fernet-encrypted
    groq_api_key = Column(Text, nullable=True)  # Fernet-encrypted
    trending_fallback_groq_api_key = Column(Text, nullable=True)
    blog_fallback_groq_api_key = Column(Text, nullable=True)
    serpapi_api_key = Column(Text, nullable=True)
    tavily_api_key = Column(Text, nullable=True)
    mangools_api_token = Column(Text, nullable=True)
    apify_api_key = Column(Text, nullable=True)
    preferred_provider = Column(String, nullable=False, default="default", server_default="default")
    preferred_model = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="llm_settings")
