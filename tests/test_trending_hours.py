"""Trending look-back window (`hours`) validation tests."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.auth_service import create_access_token, get_password_hash

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


@pytest.fixture()
def auth_headers(db_session):
    from app.models.user import User

    user = User(
        email="hours@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Hours Tester",
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("hours", [4, 24, 48, 168])
def test_list_trending_accepts_allowed_windows(client, auth_headers, hours):
    response = client.get(
        f"/api/trending/keywords?source=trending&hours={hours}",
        headers=auth_headers,
    )
    assert response.status_code == 200


@pytest.mark.parametrize("hours", [3, 5, 12, 72, 200])
def test_list_trending_rejects_other_windows(client, auth_headers, hours):
    response = client.get(
        f"/api/trending/keywords?source=trending&hours={hours}",
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "hours must be one of" in response.json()["detail"]


def test_list_manual_ignores_hours(client, auth_headers):
    response = client.get(
        "/api/trending/keywords?source=manual&hours=13",
        headers=auth_headers,
    )
    assert response.status_code == 200


def test_fetch_keywords_rejects_bad_window(client, auth_headers):
    response = client.post(
        "/api/trending/keywords/fetch?hours=13",
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "hours must be one of" in response.json()["detail"]


def test_fetch_keywords_passes_allowed_window(client, auth_headers, monkeypatch):
    captured = {}

    async def fake_fetch(db, hours, *, api_key=None, groq_fallback_key=None):
        captured["hours"] = hours
        return []

    monkeypatch.setattr("app.api.trending.resolve_api_key", lambda *a, **k: "serp-key")
    monkeypatch.setattr(
        "app.api.trending.resolve_api_key_optional", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.api.trending.fetch_trending_keywords", AsyncMock(side_effect=fake_fetch)
    )

    response = client.post(
        "/api/trending/keywords/fetch?hours=168",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert captured["hours"] == 168
