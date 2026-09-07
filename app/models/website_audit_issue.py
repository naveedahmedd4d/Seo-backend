from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship

from app.database import Base


class WebsiteAuditIssue(Base):
    __tablename__ = "website_audit_issues"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(
        Integer, ForeignKey("website_audit_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id = Column(
        Integer, ForeignKey("website_audit_pages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    url = Column(String(2048), nullable=False)
    category = Column(String(64), nullable=False, index=True)
    issue_code = Column(String(64), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    severity = Column(String(16), nullable=False, index=True)
    priority_score = Column(Float, nullable=False, default=0.0)
    why_it_matters = Column(Text, nullable=False)
    recommendation = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=True)

    run = relationship("WebsiteAuditRun", back_populates="issues")
    page = relationship("WebsiteAuditPage", back_populates="issues")
