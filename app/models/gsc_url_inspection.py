from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class GscUrlInspection(Base):
    __tablename__ = "gsc_url_inspections"
    __table_args__ = (
        UniqueConstraint("property_id", "url", name="uq_gsc_url_inspection"),
    )

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("gsc_properties.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(String, nullable=False)
    verdict = Column(String(32), nullable=True)
    coverage_state = Column(String(255), nullable=True)
    indexing_state = Column(String(64), nullable=True)
    robots_txt_state = Column(String(64), nullable=True)
    page_fetch_state = Column(String(64), nullable=True)
    google_canonical = Column(Text, nullable=True)
    user_canonical = Column(Text, nullable=True)
    last_crawl_time = Column(DateTime(timezone=True), nullable=True)
    raw_response = Column(JSON, nullable=True)
    inspected_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    property = relationship("GscProperty", back_populates="url_inspections")
