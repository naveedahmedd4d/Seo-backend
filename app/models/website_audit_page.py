from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship

from app.database import Base


class WebsiteAuditPage(Base):
    __tablename__ = "website_audit_pages"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(
        Integer, ForeignKey("website_audit_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url = Column(String(2048), nullable=False)
    wp_post_id = Column(Integer, nullable=True)
    post_type = Column(String(32), nullable=True)
    status_code = Column(Integer, nullable=True)
    signals_json = Column(JSON, nullable=True)
    title = Column(Text, nullable=True)
    meta_description = Column(Text, nullable=True)
    word_count = Column(Integer, nullable=False, default=0)
    thin_score = Column(Float, nullable=True)

    run = relationship("WebsiteAuditRun", back_populates="pages")
    issues = relationship("WebsiteAuditIssue", back_populates="page")
