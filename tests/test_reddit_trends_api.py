from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.reddit_trends import RedditPost, RedditTrend, RedditTrendPost
from app.models.trending_keywords import TrendingKeywords
from app.models.user import User
from app.services.auth_service import create_access_token, get_password_hash
from app.services.reddit_trend_processing import utcnow
from app.services.reddit_trends_service import create_reddit_run, execute_reddit_run


engine = create_engine(
    "sqlite://",
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
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def make_user(db, email: str):
    user = User(
        email=email,
        password_hash=get_password_hash("password123"),
        full_name="Reddit Tester",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token({"sub": user.email})
    return user, {"Authorization": f"Bearer {token}"}


def make_trend(db, user: User, suffix: str, subreddit: str):
    now = utcnow()
    keyword = TrendingKeywords(
        user_id=user.id,
        keyword=f"Reddit topic {suffix}",
        source="reddit",
        category="Technology & AI",
        data={},
        fetched_at=now,
    )
    post = RedditPost(
        user_id=user.id,
        reddit_post_id=f"post-{suffix}",
        canonical_url=f"https://www.reddit.com/r/{subreddit}/comments/{suffix}/topic/",
        title=f"Reddit topic {suffix}",
        normalized_title=f"reddit topic {suffix}",
        subreddit=subreddit,
        score=100,
        comments=30,
        score_per_hour=50,
        comments_per_hour=15,
        upvote_ratio=0.9,
        engagement_total=130,
        created_utc=now - timedelta(hours=1),
        retrieved_at=now,
    )
    db.add_all([keyword, post])
    db.flush()
    trend = RedditTrend(
        user_id=user.id,
        trending_keyword_id=keyword.id,
        representative_post_id=post.id,
        fingerprint=(suffix * 64)[:64],
        topic=keyword.keyword,
        summary="Summary",
        category="Technology & AI",
        topic_type="OpenAI",
        primary_subreddit=subreddit,
        subreddit_mentions=1,
        post_score=100,
        comments=30,
        score_per_hour=50,
        comments_per_hour=15,
        upvote_ratio=0.9,
        engagement_total=130,
        trend_score=90,
        latest_post_at=now,
    )
    db.add(trend)
    db.flush()
    db.add(RedditTrendPost(reddit_trend_id=trend.id, reddit_post_id=post.id))
    db.commit()
    return trend


def test_reddit_trends_are_account_isolated(client, db_session):
    owner, owner_headers = make_user(db_session, "reddit-owner@test.com")
    other, other_headers = make_user(db_session, "reddit-other@test.com")
    make_trend(db_session, owner, "a", "technology")
    make_trend(db_session, other, "b", "OpenAI")

    owner_response = client.get("/api/trending/reddit/trends", headers=owner_headers)
    other_response = client.get("/api/trending/reddit/trends", headers=other_headers)
    assert owner_response.status_code == 200
    assert other_response.status_code == 200
    assert [row["keyword"] for row in owner_response.json()["trends"]] == ["Reddit topic a"]
    assert [row["keyword"] for row in other_response.json()["trends"]] == ["Reddit topic b"]


def test_duplicate_run_returns_existing_active_run(db_session):
    user, _ = make_user(db_session, "reddit-runs@test.com")
    first, first_cached = create_reddit_run(db_session, user.id)
    second, second_cached = create_reddit_run(db_session, user.id, force=True)
    assert not first_cached
    assert not second_cached
    assert second.id == first.id
    assert second.status == "pending"


def test_recent_successful_run_is_reused_without_force(db_session):
    user, _ = make_user(db_session, "reddit-cache@test.com")
    run, _ = create_reddit_run(db_session, user.id)
    run.status = "succeeded"
    run.stage = "completed"
    run.completed_at = utcnow()
    db_session.commit()

    cached_run, cached = create_reddit_run(db_session, user.id)
    assert cached is True
    assert cached_run.id == run.id


def test_settings_reject_empty_collection_scope(client, db_session):
    _, headers = make_user(db_session, "reddit-settings@test.com")
    response = client.patch(
        "/api/trending/reddit/settings",
        headers=headers,
        json={"queries": [], "subreddits": []},
    )
    assert response.status_code == 400
    assert "at least one" in response.json()["detail"]


@pytest.mark.asyncio
async def test_provider_configuration_failure_marks_run_failed(db_session, monkeypatch):
    user, _ = make_user(db_session, "reddit-failure@test.com")
    run, _ = create_reddit_run(db_session, user.id)

    def fail_key(*_args, **_kwargs):
        raise ValueError("Add your Apify API key in Settings")

    monkeypatch.setattr("app.services.reddit_trends_service.resolve_api_key", fail_key)
    await execute_reddit_run(db_session, user.id, run.id)
    db_session.refresh(run)
    assert run.status == "failed"
    assert "Apify API key" in run.error_message


def test_sources_endpoint_cannot_read_another_account(client, db_session):
    owner, _ = make_user(db_session, "reddit-source-owner@test.com")
    other, other_headers = make_user(db_session, "reddit-source-other@test.com")
    trend = make_trend(db_session, owner, "c", "technology")
    response = client.get(
        f"/api/trending/reddit/trends/{trend.id}/sources",
        headers=other_headers,
    )
    assert response.status_code == 404
