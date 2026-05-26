from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class WordPressSite(Base):
    __tablename__ = "wordpress_sites"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    api_key = Column(Text, nullable=False)  # Encrypted API key
    username = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="wordpress_sites")
    generated_content = relationship("GeneratedContent", back_populates="wordpress_site", cascade="all, delete-orphan")

