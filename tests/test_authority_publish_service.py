"""Tests for authority_publish_service routing."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.authority_site import (
    AuthorityBlogPost,
    AuthorityNodeStatus,
    AuthorityServiceCluster,
    AuthoritySite,
)
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.services import authority_publish_service as pub
from app.services.auth_service import get_password_hash
from app.services.authority_publish_service import get_recommended_publish_order
from app.services.wordpress_service import encrypt_api_key

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def user_and_wp(db_session):
    user = User(
        email="publish@test.com",
        password_hash=get_password_hash("password123"),
    )
    db_session.add(user)
    db_session.flush()
    wp = WordPressSite(
        user_id=user.id,
        name="Test Site",
        url="https://example.com",
        api_key=encrypt_api_key("test-key"),
        username="admin",
    )
    db_session.add(wp)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(wp)
    return user, wp


def _site_bundle():
    site = AuthoritySite(
        id=1,
        user_id=1,
        wordpress_site_id=1,
        money_page_title="Money",
        money_page_slug="money",
        money_page_focus_keyword="money kw",
        money_page_status=AuthorityNodeStatus.DRAFT,
        money_page_body="# Money content",
    )
    cluster = AuthorityServiceCluster(
        id=2,
        authority_site_id=1,
        title="Service",
        slug="service",
        focus_keyword="service kw",
        status=AuthorityNodeStatus.DRAFT,
        color="blue",
        sort_order=0,
        body="# Service content",
    )
    blog = AuthorityBlogPost(
        id=3,
        cluster_id=2,
        title="Blog",
        slug="blog",
        focus_keyword="blog kw",
        status=AuthorityNodeStatus.DRAFT,
        sort_order=0,
        body="# Blog content",
    )
    cluster.blog_posts = [blog]
    site.service_clusters = [cluster]
    site.link_edges = []
    wp = WordPressSite(id=1, user_id=1, name="Site", url="https://x.com", api_key="k", username="u")
    return site, cluster, blog, wp


def test_recommended_publish_order():
    site, cluster, blog, _ = _site_bundle()
    order = get_recommended_publish_order(site)
    assert order[0] == ("money", 1)
    assert ("service", cluster.id) in order
    assert ("blog", blog.id) in order
    assert order.index(("money", 1)) < order.index(("service", cluster.id))
    assert order.index(("service", cluster.id)) < order.index(("blog", blog.id))


@pytest.mark.asyncio
async def test_publish_money_page_uses_pages_endpoint(db_session, user_and_wp):
    user, wp = user_and_wp
    from app.services import authority_service

    site = authority_service.create_authority_site(
        db_session, user.id, wp.id, "Money", "money kw"
    )
    site = authority_service.get_authority_site(db_session, site.id, user.id)
    site.money_page_body = "# Content"
    site.money_page_status = AuthorityNodeStatus.DRAFT
    db_session.commit()

    with patch(
        "app.services.authority_publish_service.publish_page_to_wordpress",
        new_callable=AsyncMock,
        return_value={"success": True, "page_id": 99, "link": "https://x.com/money"},
    ), patch(
        "app.services.authority_publish_service._assemble_money_content",
        new_callable=AsyncMock,
        return_value="<p>html</p>",
    ):
        result = await pub.publish_money_page(db_session, site.id, user.id, "draft")
    assert result.money_page_status == AuthorityNodeStatus.PUBLISHED
    assert result.money_page_wp_page_id == 99


@pytest.mark.asyncio
async def test_publish_blog_uses_posts_endpoint(db_session, user_and_wp):
    user, wp = user_and_wp
    from app.services import authority_service

    site = authority_service.create_authority_site(
        db_session, user.id, wp.id, "Money", "money kw"
    )
    authority_service.create_cluster(
        db_session, site.id, user.id, "Service", "service kw"
    )
    site = authority_service.get_authority_site(db_session, site.id, user.id)
    blog = site.service_clusters[0].blog_posts[0]
    blog.body = "# Blog"
    blog.status = AuthorityNodeStatus.DRAFT
    db_session.commit()

    with patch(
        "app.services.authority_publish_service.publish_to_wordpress",
        new_callable=AsyncMock,
        return_value={"success": True, "post_id": 55, "link": "https://x.com/blog"},
    ), patch(
        "app.services.authority_publish_service._assemble_blog_content",
        new_callable=AsyncMock,
        return_value="<p>html</p>",
    ):
        result = await pub.publish_blog_post(db_session, blog.id, user.id, "draft")
    updated_blog = result.service_clusters[0].blog_posts[0]
    assert updated_blog.status == AuthorityNodeStatus.PUBLISHED
    assert updated_blog.wp_post_id == 55


@pytest.mark.asyncio
async def test_publish_money_requires_body(db_session, user_and_wp):
    user, wp = user_and_wp
    from app.services import authority_service

    site = authority_service.create_authority_site(
        db_session, user.id, wp.id, "Money", "money kw"
    )
    with pytest.raises(HTTPException) as exc:
        await pub.publish_money_page(db_session, site.id, user.id)
    assert exc.value.status_code == 400
