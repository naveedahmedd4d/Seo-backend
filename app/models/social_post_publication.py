from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class SocialPostPublication(Base):
    __tablename__ = "social_post_publications"
    __table_args__ = (
        UniqueConstraint("history_id", "platform", name="uq_social_post_publications_history_platform"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    history_id = Column(Integer, ForeignKey("social_post_history.id", ondelete="CASCADE"), nullable=False)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id", ondelete="SET NULL"), nullable=True)
    platform = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False)  # success | failed
    external_id = Column(String(256), nullable=True)
    external_url = Column(String(1024), nullable=True)
    error = Column(Text, nullable=True)
    remote_status = Column(String(32), nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")
    history = relationship("SocialPostHistory", back_populates="publications")
    social_account = relationship("SocialAccount")
