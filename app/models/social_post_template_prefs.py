from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class SocialPostTemplatePrefs(Base):
    """Per-user brand template presets for social post cards (full store JSON)."""

    __tablename__ = "social_post_template_prefs"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    store = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="social_post_template_prefs")
