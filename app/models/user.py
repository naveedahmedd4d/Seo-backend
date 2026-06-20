from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    wordpress_sites = relationship("WordPressSite", back_populates="user", cascade="all, delete-orphan")
    generated_content = relationship("GeneratedContent", back_populates="user", cascade="all, delete-orphan")
    research_history = relationship("ResearchHistory", back_populates="user", cascade="all, delete-orphan")
    image_search_history = relationship("ImageSearchHistory", back_populates="user", cascade="all, delete-orphan")
    social_post_history = relationship("SocialPostHistory", back_populates="user", cascade="all, delete-orphan")
    social_accounts = relationship("SocialAccount", back_populates="user", cascade="all, delete-orphan")
    social_platform_apps = relationship("SocialPlatformApp", back_populates="user", cascade="all, delete-orphan")
