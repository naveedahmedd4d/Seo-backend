from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class PublishedPost(Base):
    """Tracks every article published to WordPress.

    One row per (workflow, blog_key, wordpress_site) combination.
    Enables duplicate prevention, status display, and future
    multi-site publishing without touching workflow state_data.
    """
    __tablename__ = "published_posts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    workflow_id = Column(Integer, ForeignKey("workflow_states.id", ondelete="CASCADE"), nullable=False, index=True)
    wordpress_site_id = Column(Integer, ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=False)
    blog_key = Column(String(20), nullable=False)  # "parent", "child_0", "child_1"
    wp_post_id = Column(Integer, nullable=False)
    wp_link = Column(Text, nullable=True)
    wp_status = Column(String(20), nullable=False, default="draft")  # "draft" or "publish"
    title = Column(Text, nullable=True)
    target_keyword = Column(String(512), nullable=True)
    source_category = Column(String(255), nullable=True)
    wp_author_id = Column(Integer, nullable=True)
    wp_author_name = Column(String(255), nullable=True)
    canonical_url = Column(Text, nullable=True)
    remote_status = Column(String(32), nullable=True)
    wordpress_published_at = Column(DateTime(timezone=True), nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), server_default=func.now())

    # Prevent publishing same article to same site twice
    __table_args__ = (
        UniqueConstraint("workflow_id", "blog_key", "wordpress_site_id", name="uq_publish_once"),
    )

    # Relationships
    user = relationship("User")
    workflow = relationship("WorkflowState")
    wordpress_site = relationship("WordPressSite")
