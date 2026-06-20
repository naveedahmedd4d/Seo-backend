"""Social accounts API tests."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.auth_service import create_access_token
from app.services.social_media_storage import save_png_base64, public_image_url

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
    from app.services.auth_service import get_password_hash

    user = User(
        email="social@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Social Tester",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def test_list_social_accounts_empty(client, auth_headers):
    response = client.get("/api/social-accounts", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_pinterest_connect_requires_credentials(client, auth_headers):
    response = client.post(
        "/api/social-accounts/pinterest/connect",
        headers=auth_headers,
        json={"client_id": "your_app_id", "client_secret": "your_app_secret"},
    )
    assert response.status_code == 400


def test_pinterest_setup_returns_redirect_uri(client, auth_headers):
    response = client.get("/api/social-accounts/pinterest/setup", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "redirect_uri" in data
    assert data["has_app_credentials"] is False


def test_x_setup_returns_redirect_uri(client, auth_headers):
    response = client.get("/api/social-accounts/x/setup", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "/api/social-accounts/x/callback" in data["redirect_uri"]


def test_x_connect_rejects_placeholder(client, auth_headers):
    response = client.post(
        "/api/social-accounts/x/connect",
        headers=auth_headers,
        json={"client_id": "your_app_id", "client_secret": "your_app_secret"},
    )
    assert response.status_code == 400


def test_mastodon_setup_returns_redirect_uri(client, auth_headers):
    response = client.get("/api/social-accounts/mastodon/setup", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "/api/social-accounts/mastodon/callback" in data["redirect_uri"]


def test_mastodon_connect_requires_instance(client, auth_headers):
    response = client.post(
        "/api/social-accounts/mastodon/connect",
        headers=auth_headers,
        json={
            "instance_url": "",
            "client_id": "abc123",
            "client_secret": "secret123456",
        },
    )
    assert response.status_code == 400


def test_bluesky_connect_requires_fields(client, auth_headers):
    response = client.post(
        "/api/social-accounts/bluesky/connect",
        headers=auth_headers,
        json={"identifier": "", "app_password": ""},
    )
    assert response.status_code == 400


def test_devto_connect_requires_api_key(client, auth_headers):
    response = client.post(
        "/api/social-accounts/devto/connect",
        headers=auth_headers,
        json={"api_key": ""},
    )
    assert response.status_code == 400


def test_hashnode_connect_requires_api_key(client, auth_headers):
    response = client.post(
        "/api/social-accounts/hashnode/connect",
        headers=auth_headers,
        json={"api_key": ""},
    )
    assert response.status_code == 400


def test_blogger_setup_returns_redirect_uri(client, auth_headers):
    response = client.get(
        "/api/social-accounts/blogger/setup",
        headers={
            **auth_headers,
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "api.example.com",
        },
    )
    assert response.status_code == 200
    assert response.json()["redirect_uri"] == (
        "https://api.example.com/api/social-accounts/blogger/callback"
    )


def test_reddit_setup_returns_redirect_uri(client, auth_headers):
    response = client.get("/api/social-accounts/reddit/setup", headers=auth_headers)
    assert response.status_code == 200
    assert "/api/social-accounts/reddit/callback" in response.json()["redirect_uri"]


def test_reddit_connect_rejects_placeholder(client, auth_headers):
    response = client.post(
        "/api/social-accounts/reddit/connect",
        headers=auth_headers,
        json={"client_id": "your_app_id", "client_secret": "your_app_secret"},
    )
    assert response.status_code == 400


def test_social_media_storage_roundtrip(monkeypatch):
    monkeypatch.setattr(
        "app.services.social_media_storage.settings.api_public_url",
        "http://localhost:8000",
    )
    # Minimal valid PNG header + IHDR chunk padding
    import base64

    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    encoded = base64.b64encode(png_bytes).decode()
    filename = save_png_base64(encoded)
    assert filename.endswith(".png")
    assert public_image_url(filename).startswith("http://localhost:8000/api/social-post/media/")
