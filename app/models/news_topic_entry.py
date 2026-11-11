from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class NewsTopicEntry(Base):
    """One feed mention of a daily IT/AI topic, shown on the Trending page."""

    __tablename__ = "news_topic_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic_date = Column(Date, nullable=False, index=True)
    title = Column(Text, nullable=False)
    name = Column(String(120), nullable=False)
    source = Column(String(40), nullable=False)
    link = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")
