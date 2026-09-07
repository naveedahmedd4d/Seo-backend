from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class BlogContentAuditFix(Base):
    __tablename__ = "blog_content_audit_fixes"

    id = Column(Integer, primary_key=True, index=True)
    audit_id = Column(
        Integer, ForeignKey("blog_content_audits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fix_type = Column(String(64), nullable=False)
    target_field = Column(String(32), nullable=False, default="content")
    title = Column(String(512), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(32), nullable=False, default="medium")
    status = Column(String(32), nullable=False, default="proposed")
    finding_refs = Column(JSON, nullable=True)
    before_text = Column(Text, nullable=True)
    after_proposed = Column(Text, nullable=True)
    diff_segments = Column(JSON, nullable=True)
    generator_meta = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    audit = relationship("BlogContentAudit", back_populates="fixes")
