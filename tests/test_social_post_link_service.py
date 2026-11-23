"""Tests for first-party Facebook destination-link selection."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.published_post import PublishedPost
from app.models.social_post_history import SocialPostHistory
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.models.workflow_state import WorkflowState, WorkflowStatus, WorkflowType
from app.services.social_post_link_service import resolve_facebook_destination_url


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(bind=engine)


@pytest.fixture()
def link_db():
    Base.metadata.create_all(engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def _seed(
    link_db,
    *,
    query="Bryce Miller Mariners",
    related=False,
    news_link="https://external-news.example/story",
    settings_snapshot=None,
):
    user = User(email="links@example.com", password_hash="x")
    link_db.add(user)
    link_db.flush()
    site = WordPressSite(
        user_id=user.id,
        name="TrendifyPress",
        url="https://trendifypress.com/",
        username="editor",
        api_key="encrypted",
    )
    link_db.add(site)
    link_db.flush()
    workflow = WorkflowState(
        user_id=user.id,
        workflow_type=WorkflowType.BLOG,
        status=WorkflowStatus.COMPLETED,
        state_data={},
    )
    link_db.add(workflow)
    link_db.flush()
    relevant = PublishedPost(
        user_id=user.id,
        workflow_id=workflow.id,
        wordpress_site_id=site.id,
        blog_key="parent",
        wp_post_id=10,
        wp_link="https://trendifypress.com/bryce-miller-mariners/",
        canonical_url="https://trendifypress.com/bryce-miller-mariners/",
        wp_status="publish",
        title="Bryce Miller leads the Seattle Mariners",
        target_keyword="Bryce Miller Mariners",
        published_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )
    link_db.add(relevant)
    link_db.flush()
    history = SocialPostHistory(
        user_id=user.id,
        query=query,
        headline=f"{query} update",
        description="Latest story",
        news_results=[{"link": news_link}] if news_link else [],
        related_published_post_id=relevant.id if related else None,
        settings_snapshot=settings_snapshot,
    )
    link_db.add(history)
    link_db.commit()
    return user, site, relevant, history


@pytest.mark.asyncio
async def test_explicit_related_article_is_used_first(link_db):
    user, _site, relevant, history = _seed(link_db, related=True)
    result = await resolve_facebook_destination_url(link_db, user.id, history)
    assert result == relevant.canonical_url


@pytest.mark.asyncio
async def test_standalone_social_post_has_no_blog_url(link_db):
    """Topic/social posts must not auto-attach a WordPress or homepage link."""
    user, _site, _relevant, history = _seed(link_db)
    result = await resolve_facebook_destination_url(link_db, user.id, history)
    assert result is None


@pytest.mark.asyncio
async def test_social_post_ignores_first_party_news_link(link_db):
    """Even a WordPress URL in news_results must not appear on Social Posts."""
    user, _site, _relevant, history = _seed(
        link_db,
        query="Apple AI agent",
        news_link="https://trendifypress.com/google-gives-gemini-the-ability/",
        settings_snapshot={"post_type": "social"},
    )
    result = await resolve_facebook_destination_url(link_db, user.id, history)
    assert result is None


@pytest.mark.asyncio
async def test_first_party_news_link_is_used_for_blog_promotion(link_db):
    user, _site, _relevant, history = _seed(
        link_db,
        query="Quantum battery breakthrough",
        news_link="https://trendifypress.com/quantum-battery-guide/",
        settings_snapshot={"match_blog": True},
    )
    result = await resolve_facebook_destination_url(link_db, user.id, history)
    assert result == "https://trendifypress.com/quantum-battery-guide/"


@pytest.mark.asyncio
async def test_post_type_blog_uses_first_party_news_link(link_db):
    user, _site, _relevant, history = _seed(
        link_db,
        query="Quantum battery breakthrough",
        news_link="https://trendifypress.com/quantum-battery-guide/",
        settings_snapshot={"post_type": "blog"},
    )
    result = await resolve_facebook_destination_url(link_db, user.id, history)
    assert result == "https://trendifypress.com/quantum-battery-guide/"


@pytest.mark.asyncio
async def test_external_news_link_is_never_used(link_db):
    user, _site, _relevant, history = _seed(
        link_db,
        query="Quantum battery breakthrough",
        news_link="https://external-news.example/quantum",
        settings_snapshot={"match_blog": True},
    )
    result = await resolve_facebook_destination_url(link_db, user.id, history)
    assert result is None
