from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class ContentStatus(str, enum.Enum):
    DRAFT = "draft"
    GENERATED = "generated"
    PUBLISHED = "published"
    FAILED = "failed"


class GeneratedContent(Base):
    __tablename__ = "generated_content"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    wordpress_site_id = Column(Integer, ForeignKey("wordpress_sites.id"), nullable=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    status = Column(Enum(ContentStatus), default=ContentStatus.DRAFT, nullable=False)
    wordpress_post_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="generated_content")
    wordpress_site = relationship("WordPressSite", back_populates="generated_content")

