from sqlalchemy import Column, Integer, String, DateTime, JSON
from sqlalchemy.sql import func
from app.database import Base


class TrendingKeywords(Base):
    __tablename__ = "trending_keywords"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String, nullable=False, index=True)
    data = Column(JSON, nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    category = Column(String, nullable=True, index=True)
    blog_workflow_id = Column(Integer, nullable=True, index=True)

