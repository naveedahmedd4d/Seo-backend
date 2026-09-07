from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class UserBlogSettings(Base):
    """Per-user blog generation defaults (length, SEO extras, CTA, images)."""

    __tablename__ = "user_blog_settings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    target_word_count = Column(Integer, nullable=True)
    intro_length = Column(String, nullable=False, default="standard", server_default="standard")
    conclusion_length = Column(String, nullable=False, default="standard", server_default="standard")
    faq_count = Column(Integer, nullable=False, default=5, server_default="5")
    related_query_count = Column(Integer, nullable=False, default=8, server_default="8")
    include_site_cta = Column(Boolean, nullable=False, default=True, server_default="true")
    cta_text = Column(String, nullable=True)
    cta_href = Column(String, nullable=True)
    auto_featured_image = Column(Boolean, nullable=False, default=True, server_default="true")
    max_section_images = Column(Integer, nullable=False, default=3, server_default="3")
    creativity = Column(String, nullable=False, default="balanced", server_default="balanced")
    custom_style_notes = Column(Text, nullable=True)
    seo_power_words = Column(Boolean, nullable=False, default=True, server_default="true")
    last_updated_footer = Column(Boolean, nullable=False, default=True, server_default="true")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="blog_settings")


class UserBlogTemplate(Base):
    """Per-user outline template for one article type."""

    __tablename__ = "user_blog_templates"
    __table_args__ = (
        UniqueConstraint("user_id", "article_type", name="uq_user_blog_templates_user_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    article_type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    is_default = Column(Boolean, nullable=False, default=True, server_default="true")
    target_word_count = Column(Integer, nullable=False)
    sections = Column(JSON, nullable=False)
    format_options = Column(JSON, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="blog_templates")
