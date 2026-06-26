import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class AuthorityNodeStatus(str, enum.Enum):
    PLANNED = "planned"
    DRAFT = "draft"
    PUBLISHED = "published"


class AuthorityLinkType(str, enum.Enum):
    BLOG_TO_SERVICE = "blog_to_service"
    SERVICE_TO_MONEY = "service_to_money"
    BLOG_TO_BLOG = "blog_to_blog"


class AuthorityLinkStatus(str, enum.Enum):
    PLANNED = "planned"
    INJECTED = "injected"
    PUBLISHED = "published"


# Store as VARCHAR — avoids PostgreSQL native enum mismatches across Alembic/startup.
NodeStatusColumn = Enum(AuthorityNodeStatus, native_enum=False, length=20)
LinkTypeColumn = Enum(AuthorityLinkType, native_enum=False, length=30)
LinkStatusColumn = Enum(AuthorityLinkStatus, native_enum=False, length=20)


class AuthoritySite(Base):
    __tablename__ = "authority_sites"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    wordpress_site_id = Column(
        Integer, ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    money_page_title = Column(String, nullable=False)
    money_page_slug = Column(String, nullable=False)
    money_page_focus_keyword = Column(String, nullable=False)
    money_page_status = Column(
        NodeStatusColumn, nullable=False, default=AuthorityNodeStatus.PLANNED
    )
    money_page_body = Column(Text, nullable=True)
    money_page_meta_description = Column(Text, nullable=True)
    money_page_seo_title = Column(String, nullable=True)
    money_page_wp_page_id = Column(Integer, nullable=True)
    money_page_wp_link = Column(Text, nullable=True)
    money_page_generation_error = Column(Text, nullable=True)
    money_page_is_generating = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "wordpress_site_id", name="uq_authority_site_per_wp"),
    )

    user = relationship("User", back_populates="authority_sites")
    wordpress_site = relationship("WordPressSite")
    service_clusters = relationship(
        "AuthorityServiceCluster",
        back_populates="authority_site",
        cascade="all, delete-orphan",
        order_by="AuthorityServiceCluster.sort_order",
    )
    link_edges = relationship(
        "AuthorityLinkEdge",
        back_populates="authority_site",
        cascade="all, delete-orphan",
    )
    published_nodes = relationship(
        "AuthorityPublishedNode",
        back_populates="authority_site",
        cascade="all, delete-orphan",
    )


class AuthorityServiceCluster(Base):
    __tablename__ = "authority_service_clusters"

    id = Column(Integer, primary_key=True, index=True)
    authority_site_id = Column(
        Integer, ForeignKey("authority_sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = Column(String, nullable=False)
    slug = Column(String, nullable=False)
    focus_keyword = Column(String, nullable=False)
    status = Column(NodeStatusColumn, nullable=False, default=AuthorityNodeStatus.PLANNED)
    color = Column(String(20), nullable=False, default="blue")
    sort_order = Column(Integer, nullable=False, default=0)
    body = Column(Text, nullable=True)
    meta_description = Column(Text, nullable=True)
    seo_title = Column(String, nullable=True)
    wp_page_id = Column(Integer, nullable=True)
    wp_link = Column(Text, nullable=True)
    generation_error = Column(Text, nullable=True)
    is_generating = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    authority_site = relationship("AuthoritySite", back_populates="service_clusters")
    blog_posts = relationship(
        "AuthorityBlogPost",
        back_populates="cluster",
        cascade="all, delete-orphan",
        order_by="AuthorityBlogPost.sort_order",
    )


class AuthorityBlogPost(Base):
    __tablename__ = "authority_blog_posts"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(
        Integer, ForeignKey("authority_service_clusters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = Column(String, nullable=False)
    slug = Column(String, nullable=False)
    focus_keyword = Column(String, nullable=False)
    article_type = Column(String(50), nullable=True)
    status = Column(NodeStatusColumn, nullable=False, default=AuthorityNodeStatus.PLANNED)
    sort_order = Column(Integer, nullable=False, default=0)
    body = Column(Text, nullable=True)
    meta_description = Column(Text, nullable=True)
    seo_title = Column(String, nullable=True)
    wp_post_id = Column(Integer, nullable=True)
    wp_link = Column(Text, nullable=True)
    generation_error = Column(Text, nullable=True)
    is_generating = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    cluster = relationship("AuthorityServiceCluster", back_populates="blog_posts")


class AuthorityLinkEdge(Base):
    __tablename__ = "authority_link_edges"

    id = Column(Integer, primary_key=True, index=True)
    authority_site_id = Column(
        Integer, ForeignKey("authority_sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type = Column(String(20), nullable=False)
    source_id = Column(Integer, nullable=False)
    target_type = Column(String(20), nullable=False)
    target_id = Column(Integer, nullable=False)
    link_type = Column(LinkTypeColumn, nullable=False)
    anchor_text = Column(Text, nullable=True)
    status = Column(LinkStatusColumn, nullable=False, default=AuthorityLinkStatus.PLANNED)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    authority_site = relationship("AuthoritySite", back_populates="link_edges")


class AuthorityPublishedNode(Base):
    __tablename__ = "authority_published_nodes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    authority_site_id = Column(
        Integer, ForeignKey("authority_sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wordpress_site_id = Column(
        Integer, ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_type = Column(String(20), nullable=False)
    node_id = Column(Integer, nullable=False)
    wp_content_type = Column(String(10), nullable=False)
    wp_content_id = Column(Integer, nullable=False)
    wp_link = Column(Text, nullable=True)
    wp_status = Column(String(20), nullable=False, default="draft")
    title = Column(Text, nullable=True)
    published_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "node_type", "node_id", "wordpress_site_id", name="uq_authority_published_node"
        ),
    )

    user = relationship("User")
    authority_site = relationship("AuthoritySite", back_populates="published_nodes")
    wordpress_site = relationship("WordPressSite")
