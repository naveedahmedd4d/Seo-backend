from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class GscProperty(Base):
    __tablename__ = "gsc_properties"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    site_url = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    wordpress_site_id = Column(Integer, ForeignKey("wordpress_sites.id", ondelete="SET NULL"), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="gsc_properties")
    wordpress_site = relationship("WordPressSite")
    page_metrics = relationship("GscPageMetric", back_populates="property", cascade="all, delete-orphan")
    query_metrics = relationship("GscQueryMetric", back_populates="property", cascade="all, delete-orphan")
    action_items = relationship("SeoActionItem", back_populates="property", cascade="all, delete-orphan")
