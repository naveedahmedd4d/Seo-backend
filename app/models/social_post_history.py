from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class SocialPostHistory(Base):
    __tablename__ = "social_post_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    query = Column(String(512), nullable=False)
    headline = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    story_description = Column(Text, nullable=True)
    hashtags = Column(JSON, nullable=True)  # list[str]
    settings_snapshot = Column(JSON, nullable=True)  # SocialPostTemplateSettings at save time
    news_results = Column(JSON, nullable=True)  # list of NewsResultItem dicts
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="social_post_history")
