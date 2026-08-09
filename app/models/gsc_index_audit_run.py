from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class GscIndexAuditRun(Base):
    __tablename__ = "gsc_index_audit_runs"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("gsc_properties.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending")
    total_urls = Column(Integer, nullable=False, default=0)
    processed_urls = Column(Integer, nullable=False, default=0)
    not_indexed_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    warning_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    property = relationship("GscProperty", back_populates="index_audit_runs")
    user = relationship("User")
