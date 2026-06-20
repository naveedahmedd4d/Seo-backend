from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class SocialPlatformApp(Base):
    """Per-user OAuth app credentials (e.g. each user's Pinterest developer app)."""

    __tablename__ = "social_platform_apps"
    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_social_platform_app_user_platform"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String, nullable=False, index=True)
    client_id = Column(String, nullable=False)
    client_secret = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="social_platform_apps")
