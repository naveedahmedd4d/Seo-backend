"""Tests for first-party Facebook destination-link selection."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

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
from app.services.social_post_link_service import (
    relevance_score,
    resolve_facebook_destination_url,
)


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


def _seed(link_db, *, query="Bryce Miller Mariners", related=False):
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
        news_results=[{"link": "https://external-news.example/story"}],
        related_published_post_id=relevant.id if related else None,
    )
    link_db.add(history)
    link_db.commit()
    return user, site, relevant, history


def test_relevance_score_requires_meaningful_overlap():
    assert relevance_score("Bryce Miller Mariners", "Bryce Miller leads Mariners") > 0
    assert relevance_score("Bryce Miller Mariners", "Unrelated technology article") == 0


@pytest.mark.asyncio
async def test_explicit_related_article_is_used_first(link_db):
    user, _site, relevant, history = _seed(link_db, related=True)
    result = await resolve_facebook_destination_url(link_db, user.id, history)
    assert result == relevant.canonical_url


@pytest.mark.asyncio
async def test_relevant_tracked_article_replaces_external_news_url(link_db):
    user, _site, relevant, history = _seed(link_db)
    result = await resolve_facebook_destination_url(link_db, user.id, history)
    assert result == relevant.canonical_url
    assert "external-news.example" not in result


@pytest.mark.asyncio
@patch(
    "app.services.social_post_link_service._search_live_wordpress",
    new_callable=AsyncMock,
    return_value=(25, "https://trendifypress.com/quantum-battery-guide/"),
)
async def test_live_wordpress_match_is_used_when_not_tracked(mock_live_search, link_db):
    user, _site, _relevant, history = _seed(link_db, query="Quantum battery breakthrough")
    result = await resolve_facebook_destination_url(link_db, user.id, history)
    assert result == "https://trendifypress.com/quantum-battery-guide/"
    assert mock_live_search.await_args.args[1] == "Quantum battery breakthrough"


@pytest.mark.asyncio
@patch(
    "app.services.social_post_link_service._search_live_wordpress",
    new_callable=AsyncMock,
    return_value=None,
)
async def test_homepage_is_used_when_no_relevant_article(mock_live_search, link_db):
    user, site, _relevant, history = _seed(link_db, query="Quantum battery breakthrough")
    result = await resolve_facebook_destination_url(link_db, user.id, history)
    assert result == site.url.rstrip("/")
    mock_live_search.assert_awaited_once()
