from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship

from app.database import Base


class BlogContentAudit(Base):
    __tablename__ = "blog_content_audits"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(
        Integer, ForeignKey("blog_content_audit_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wp_post_id = Column(Integer, nullable=True)
    url = Column(String(2048), nullable=False)
    title = Column(Text, nullable=True)
    slug = Column(String(512), nullable=True)
    article_type = Column(String(32), nullable=False, default="unknown")
    word_count = Column(Integer, nullable=False, default=0)
    word_target = Column(Integer, nullable=False, default=1200)
    word_target_met = Column(Boolean, nullable=False, default=False)
    seo_score = Column(Float, nullable=True)
    content_quality_score = Column(Float, nullable=True)
    search_intent_score = Column(Float, nullable=True)
    content_depth_score = Column(Float, nullable=True)
    originality_score = Column(Float, nullable=True)
    eeat_score = Column(Float, nullable=True)
    adsense_quality_score = Column(Float, nullable=True)
    overall_score = Column(Float, nullable=True)
    findings = Column(JSON, nullable=True)
    gap_matrix = Column(JSON, nullable=True)
    adsense_risks = Column(JSON, nullable=True)
    audit_payload = Column(JSON, nullable=True)
    content_hash = Column(String(64), nullable=True)

    run = relationship("BlogContentAuditRun", back_populates="audits")
    fixes = relationship(
        "BlogContentAuditFix",
        back_populates="audit",
        cascade="all, delete-orphan",
    )
