from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class BlogContentAuditRun(Base):
    __tablename__ = "blog_content_audit_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    wordpress_site_id = Column(
        Integer, ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    website_audit_run_id = Column(
        Integer, ForeignKey("website_audit_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status = Column(String(32), nullable=False, default="pending")
    progress_json = Column(JSON, nullable=True)
    site_summary = Column(JSON, nullable=True)
    scope_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")
    wordpress_site = relationship("WordPressSite")
    website_audit_run = relationship("WebsiteAuditRun")
    audits = relationship(
        "BlogContentAudit",
        back_populates="run",
        cascade="all, delete-orphan",
    )
