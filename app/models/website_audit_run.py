from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class WebsiteAuditRun(Base):
    __tablename__ = "website_audit_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    wordpress_site_id = Column(
        Integer, ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status = Column(String(32), nullable=False, default="pending")
    stage = Column(String(32), nullable=False, default="discover")
    total_urls = Column(Integer, nullable=False, default=0)
    processed_urls = Column(Integer, nullable=False, default=0)
    health_score = Column(Float, nullable=True)
    category_scores = Column(JSON, nullable=True)
    scope_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")
    wordpress_site = relationship("WordPressSite")
    pages = relationship(
        "WebsiteAuditPage",
        back_populates="run",
        cascade="all, delete-orphan",
    )
    issues = relationship(
        "WebsiteAuditIssue",
        back_populates="run",
        cascade="all, delete-orphan",
    )
