from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class SeoActionItem(Base):
    __tablename__ = "seo_action_items"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("gsc_properties.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type = Column(String(32), nullable=False)
    severity = Column(String(16), nullable=False, default="info")
    page = Column(String, nullable=True)
    query = Column(String, nullable=True)
    metric_snapshot = Column(JSON, nullable=True)
    suggestion = Column(Text, nullable=True)
    impact_score = Column(Float, default=0.0, nullable=False)
    status = Column(String(32), nullable=False, default="pending_review")
    linked_published_post_id = Column(Integer, ForeignKey("published_posts.id", ondelete="SET NULL"), nullable=True)
    linked_workflow_id = Column(Integer, ForeignKey("workflow_states.id", ondelete="SET NULL"), nullable=True)
    snoozed_until = Column(DateTime(timezone=True), nullable=True)
    user_verified_at = Column(DateTime(timezone=True), nullable=True)
    detected_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    property = relationship("GscProperty", back_populates="action_items")
    user = relationship("User", back_populates="seo_action_items")
    linked_published_post = relationship("PublishedPost")
    linked_workflow = relationship("WorkflowState")
