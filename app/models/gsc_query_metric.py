from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class GscQueryMetric(Base):
    __tablename__ = "gsc_query_metrics"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("gsc_properties.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    query = Column(String, nullable=False)
    page = Column(String, nullable=False)
    clicks = Column(Integer, default=0, nullable=False)
    impressions = Column(Integer, default=0, nullable=False)
    ctr = Column(Float, default=0.0, nullable=False)
    position = Column(Float, default=0.0, nullable=False)

    property = relationship("GscProperty", back_populates="query_metrics")

    __table_args__ = (
        UniqueConstraint("property_id", "date", "query", "page", name="uq_gsc_query_metric"),
    )
