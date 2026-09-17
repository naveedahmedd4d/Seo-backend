from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship, backref
from sqlalchemy.sql import func
from app.database import Base


class TrendingKeywords(Base):
    __tablename__ = "trending_keywords"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    keyword = Column(String, nullable=False, index=True)
    data = Column(JSON, nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    category = Column(String, nullable=True, index=True)
    blog_workflow_id = Column(Integer, nullable=True, index=True)
    bio_blog_workflow_id = Column(Integer, nullable=True, index=True)
    net_worth_blog_workflow_id = Column(Integer, nullable=True, index=True)
    source = Column(String, nullable=False, server_default="trending", index=True)
    kd = Column(Integer, nullable=True, index=True)
    parent_id = Column(
        Integer,
        ForeignKey("trending_keywords.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    parent = relationship(
        "TrendingKeywords",
        remote_side=[id],
        backref=backref("subs", cascade="all, delete-orphan"),
        foreign_keys=[parent_id],
    )
    user = relationship("User", backref=backref("trending_keywords", cascade="all, delete-orphan"))
