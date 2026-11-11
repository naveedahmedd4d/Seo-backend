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


def test_linkedin_setup_returns_redirect_uri(client, auth_headers):
    response = client.get("/api/social-accounts/linkedin/setup", headers=auth_headers)
    assert response.status_code == 200
    assert "/api/social-accounts/linkedin/callback" in response.json()["redirect_uri"]


def test_facebook_setup_returns_redirect_uri(client, auth_headers):
    response = client.get("/api/social-accounts/facebook/setup", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "/api/social-accounts/facebook/callback" in data["redirect_uri"]
    assert data["client_id"] is None
    assert data.get("credentials_editable") is True
    assert data.get("can_clear_credentials") is False


def test_facebook_user_can_save_and_clear_app_credentials(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.config.settings.meta_app_id", None)
    monkeypatch.setattr("app.config.settings.meta_app_secret", None)
    monkeypatch.setattr("app.config.settings.platform_owner_email", "someone-else@example.com")

    connect = client.post(
        "/api/social-accounts/facebook/connect",
        headers=auth_headers,
        json={"client_id": "user-meta-app", "client_secret": "user-meta-secret-value"},
    )
    assert connect.status_code == 200

    setup = client.get("/api/social-accounts/facebook/setup", headers=auth_headers)
    assert setup.status_code == 200
    data = setup.json()
    assert data["has_app_credentials"] is True
    assert data["credentials_source"] == "user"
    assert data["credentials_editable"] is True
    assert data["can_clear_credentials"] is True
    assert data["client_id"] == "user-meta-app"

    cleared = client.delete("/api/social-accounts/facebook/app", headers=auth_headers)
    assert cleared.status_code == 204

    setup_after = client.get("/api/social-accounts/facebook/setup", headers=auth_headers)
    assert setup_after.status_code == 200
    assert setup_after.json()["has_app_credentials"] is False
    assert setup_after.json()["can_clear_credentials"] is False


def test_facebook_connect_uses_platform_meta_app(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.config.settings.meta_app_id", "platform-meta-app")
    monkeypatch.setattr("app.config.settings.meta_app_secret", "platform-meta-secret")

    response = client.post(
        "/api/social-accounts/facebook/connect",
        headers=auth_headers,
        json={},
    )

    assert response.status_code == 200
    authorization_url = response.json()["authorization_url"]
    assert "client_id=platform-meta-app" in authorization_url
    assert "platform-meta-secret" not in authorization_url


def test_facebook_connect_requires_platform_configuration(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.config.settings.meta_app_id", None)
    monkeypatch.setattr("app.config.settings.meta_app_secret", None)
    monkeypatch.setattr("app.config.settings.platform_owner_email", None)

    response = client.post(
        "/api/social-accounts/facebook/connect",
        headers=auth_headers,
        json={},
    )

    assert response.status_code == 400
    assert "Meta App ID" in response.json()["detail"]


def test_facebook_admin_setup_is_owner_only(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.config.settings.platform_owner_email", "owner@example.com")

    response = client.get(
        "/api/social-accounts/facebook/admin/setup",
        headers=auth_headers,
    )

    assert response.status_code == 403


def test_owner_can_save_shared_facebook_app(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.config.settings.platform_owner_email", "social@test.com")
    monkeypatch.setattr("app.config.settings.meta_app_id", None)
    monkeypatch.setattr("app.config.settings.meta_app_secret", None)

    setup_response = client.put(
        "/api/social-accounts/facebook/admin/setup",
        headers=auth_headers,
        json={"client_id": "shared-meta-app", "client_secret": "shared-meta-secret"},
    )

    assert setup_response.status_code == 200
    assert setup_response.json()["configured"] is True
    assert setup_response.json()["client_id"] == "shared-meta-app"
    assert "client_secret" not in setup_response.json()

    connect_response = client.post(
        "/api/social-accounts/facebook/connect",
        headers=auth_headers,
        json={},
    )
    assert connect_response.status_code == 200
    authorization_url = connect_response.json()["authorization_url"]
    assert "client_id=shared-meta-app" in authorization_url
    assert "shared-meta-secret" not in authorization_url
    assert "auth_type=rerequest" in authorization_url


def test_facebook_other_users_do_not_inherit_owner_meta_app(
    client, auth_headers, db_session, monkeypatch
):
    """Owner Meta keys must not authorize a different tenant's Facebook account."""
    monkeypatch.setattr("app.config.settings.platform_owner_email", "owner-only@example.com")
    monkeypatch.setattr("app.config.settings.meta_app_id", None)
    monkeypatch.setattr("app.config.settings.meta_app_secret", None)

    from app.models.user import User
    from app.services.social_platform_app_service import upsert_user_platform_app

    other = User(email="other-tenant@example.com", password_hash="x", full_name="Other")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    upsert_user_platform_app(
        db_session, other.id, "facebook", "owner-meta-app", "owner-meta-secret-value"
    )

    setup = client.get("/api/social-accounts/facebook/setup", headers=auth_headers)
    assert setup.status_code == 200
    assert setup.json()["has_app_credentials"] is False
    assert setup.json()["credentials_source"] is None

    connect = client.post(
        "/api/social-accounts/facebook/connect",
        headers=auth_headers,
        json={},
    )
    assert connect.status_code == 400
    assert "Meta App ID" in connect.json()["detail"]


def test_instagram_setup_returns_redirect_uri(client, auth_headers):
    response = client.get("/api/social-accounts/instagram/setup", headers=auth_headers)
    assert response.status_code == 200
    assert "/api/social-accounts/instagram/callback" in response.json()["redirect_uri"]


def test_instagram_connect_redirects_to_official_instagram_oauth(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr("app.config.settings.instagram_app_id", "instagram-app")
    monkeypatch.setattr("app.config.settings.instagram_app_secret", "instagram-secret")

    response = client.post(
        "/api/social-accounts/instagram/connect",
        headers=auth_headers,
        json={},
    )

    assert response.status_code == 200
    authorization_url = response.json()["authorization_url"]
    assert authorization_url.startswith("https://www.instagram.com/oauth/authorize?")
    assert "client_id=instagram-app" in authorization_url
    assert "instagram_business_basic" in authorization_url
    assert "instagram_business_content_publish" in authorization_url
    assert "enable_fb_login=0" in authorization_url
    assert "instagram-secret" not in authorization_url


def test_instagram_connect_requires_platform_configuration(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr("app.config.settings.instagram_app_id", None)
    monkeypatch.setattr("app.config.settings.instagram_app_secret", None)
    monkeypatch.setattr("app.config.settings.meta_app_id", None)
    monkeypatch.setattr("app.config.settings.meta_app_secret", None)
    monkeypatch.setattr("app.config.settings.platform_owner_email", None)

    response = client.post(
        "/api/social-accounts/instagram/connect",
        headers=auth_headers,
        json={},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Instagram connection is not configured. Contact your administrator."
    )


def test_instagram_callback_saves_account_and_returns_to_application(
    client, auth_headers, db_session, monkeypatch
):
    from app.models.user import User
    from app.services.auth_service import create_oauth_state_token

    user = db_session.query(User).filter(User.email == "social@test.com").one()
    state = create_oauth_state_token(user, "instagram")
    observed = {}

    async def fake_save(db, user_id, code, *, redirect_uri=None):
        observed.update(
            {"user_id": user_id, "code": code, "redirect_uri": redirect_uri}
        )

    monkeypatch.setattr(
        "app.api.social_accounts.save_instagram_account_from_code", fake_save
    )

    response = client.get(
        "/api/social-accounts/instagram/callback",
        params={"code": "instagram-code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code in {302, 307}
    assert response.headers["location"].endswith(
        "/dashboard/social-post?connected=instagram"
    )
    assert observed["user_id"] == user.id
    assert observed["code"] == "instagram-code"
    assert observed["redirect_uri"].endswith(
        "/api/social-accounts/instagram/callback"
    )


def test_instagram_oauth_code_creates_publish_ready_account(
    db_session, auth_headers, monkeypatch
):
    import asyncio

    from app.models.user import User
    from app.services.instagram_service import save_instagram_account_from_code
    from app.services.social_account_service import (
        get_account_metadata,
        get_decrypted_access_token,
    )
    from app.services.social_platform_app_service import OAuthAppCredentials

    user = db_session.query(User).filter(User.email == "social@test.com").one()
    credentials = OAuthAppCredentials(
        client_id="instagram-app",
        client_secret="instagram-secret",
        redirect_uri="https://api.example.com/api/social-accounts/instagram/callback",
    )

    async def fake_short_token(_credentials, _code):
        return {"access_token": "short-token", "user_id": "ig-1"}

    async def fake_long_token(_credentials, _short_token):
        return {"access_token": "long-token", "expires_in": 5_184_000}

    async def fake_profile(_path, _token, *, params=None):
        return {
            "user_id": "ig-1",
            "username": "trendify.press",
            "account_type": "BUSINESS",
        }

    monkeypatch.setattr(
        "app.services.instagram_service.get_instagram_app_credentials",
        lambda *_args, **_kwargs: credentials,
    )
    monkeypatch.setattr(
        "app.services.instagram_service.exchange_code_for_instagram_token",
        fake_short_token,
    )
    monkeypatch.setattr(
        "app.services.instagram_service.exchange_long_lived_instagram_token",
        fake_long_token,
    )
    monkeypatch.setattr(
        "app.services.instagram_service.instagram_graph_get", fake_profile
    )

    account = asyncio.run(
        save_instagram_account_from_code(
            db_session,
            user.id,
            "authorization-code",
            redirect_uri=credentials.redirect_uri,
        )
    )

    metadata = get_account_metadata(account)
    assert account.account_label == "@trendify.press"
    assert get_decrypted_access_token(account) == "long-token"
    assert metadata["auth_method"] == "instagram_login"
    assert metadata["default_instagram_id"] == "ig-1"


def test_reddit_connect_rejects_placeholder(client, auth_headers):
    response = client.post(
        "/api/social-accounts/reddit/connect",
        headers=auth_headers,
        json={"client_id": "your_app_id", "client_secret": "your_app_secret"},
    )
    assert response.status_code == 400


def test_social_media_storage_roundtrip(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.api_public_url",
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
