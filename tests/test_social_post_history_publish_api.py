"""API tests for history-first social post publishing."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.social_post_history import SocialPostHistory
from app.models.user import User
from app.schemas.social_post import SocialPostPublishResultItem

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

MINIMAL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


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
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _auth_headers(client: TestClient) -> dict[str, str]:
    client.post(
        "/api/auth/register",
        json={"email": "pub@test.com", "password": "securepass", "full_name": "Pub User"},
    )
    login = client.post(
        "/api/auth/login",
        data={"username": "pub@test.com", "password": "securepass"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _seed_history(db, user_id: int) -> int:
    row = SocialPostHistory(
        user_id=user_id,
        query="q",
        headline="Headline",
        description="desc",
        story_description="story",
        hashtags=["tag"],
        publish_status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def test_legacy_publish_returns_400(client, db_session):
    headers = _auth_headers(client)
    user = db_session.query(User).filter_by(email="pub@test.com").first()
    _seed_history(db_session, user.id)
    response = client.post(
        "/api/social-post/publish",
        headers=headers,
        json={"platforms": ["x"], "headline": "H"},
    )
    assert response.status_code == 400
    assert "history" in response.json()["detail"].lower()


def test_publish_history_not_found(client, db_session):
    headers = _auth_headers(client)
    response = client.post(
        "/api/social-post/history/9999/publish",
        headers=headers,
        json={"platforms": ["x"], "image_base64": MINIMAL_PNG_B64},
    )
    assert response.status_code == 404


def test_publish_history_missing_media(client, db_session):
    headers = _auth_headers(client)
    user = db_session.query(User).filter_by(email="pub@test.com").first()
    history_id = _seed_history(db_session, user.id)
    response = client.post(
        f"/api/social-post/history/{history_id}/publish",
        headers=headers,
        json={"platforms": ["x"]},
    )
    assert response.status_code == 400
    assert "image_base64" in response.json()["detail"].lower()


@patch("app.api.social_post.publish_history_post", new_callable=AsyncMock)
def test_publish_history_happy_path(mock_publish, client, db_session):
    headers = _auth_headers(client)
    user = db_session.query(User).filter_by(email="pub@test.com").first()
    history_id = _seed_history(db_session, user.id)
    history = db_session.query(SocialPostHistory).get(history_id)
    mock_publish.return_value = (
        [SocialPostPublishResultItem(platform="x", success=True, external_id="1")],
        history,
        "https://api.test/media/x.png",
    )

    response = client.post(
        f"/api/social-post/history/{history_id}/publish",
        headers=headers,
        json={"platforms": ["x"], "image_base64": MINIMAL_PNG_B64},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["history_status"] == "draft"
    assert body["media_url"] == "https://api.test/media/x.png"
    assert body["results"][0]["success"] is True


def test_history_list_includes_publish_status(client, db_session):
    headers = _auth_headers(client)
    user = db_session.query(User).filter_by(email="pub@test.com").first()
    _seed_history(db_session, user.id)
    response = client.get("/api/social-post/history", headers=headers)
    assert response.status_code == 200
    assert response.json()[0]["publish_status"] == "draft"
