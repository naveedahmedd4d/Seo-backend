"""Tests for owner-only platform keys and BYOK resolver."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.llm_settings_service import (
    resolve_api_key,
    resolve_llm_credentials,
    upsert_settings,
)

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


def _make_user(db_session, email: str):
    from app.models.user import User
    from app.services.auth_service import get_password_hash

    user = User(email=email, password_hash=get_password_hash("x"), full_name="T")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_non_owner_without_keys_fails(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.platform_owner.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.serpapi_key", "platform-serp"
    )
    user = _make_user(db_session, "other@example.com")
    with pytest.raises(ValueError, match="Settings"):
        resolve_api_key(db_session, user, "serpapi")


def test_non_owner_with_user_key_uses_user_not_env(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.platform_owner.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.serpapi_key", "platform-serp"
    )
    user = _make_user(db_session, "other@example.com")
    upsert_settings(db_session, user.id, serpapi_api_key="user-serp-key")
    assert resolve_api_key(db_session, user, "serpapi") == "user-serp-key"


def test_owner_without_user_key_uses_env(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.platform_owner.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.serpapi_key", "platform-serp"
    )
    user = _make_user(db_session, "hkaim1st@gmail.com")
    assert resolve_api_key(db_session, user, "serpapi") == "platform-serp"


def test_owner_prefers_user_key(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.platform_owner.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.openrouter_api_key", "platform-or"
    )
    user = _make_user(db_session, "hkaim1st@gmail.com")
    upsert_settings(db_session, user.id, openrouter_api_key="user-or-key")
    creds = resolve_llm_credentials(db_session, user.id)
    assert creds.api_key == "user-or-key"
    assert creds.source == "user"


def test_non_owner_llm_no_platform_fallback(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.platform_owner.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.openrouter_api_key", "platform-or"
    )
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.groq_api_key", "platform-groq"
    )
    user = _make_user(db_session, "other@example.com")
    with pytest.raises(ValueError, match="Settings"):
        resolve_llm_credentials(db_session, user.id)


def test_settings_api_owner_flags(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.platform_owner.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.serpapi_key", "platform-serp-xxxx"
    )

    client.post(
        "/api/auth/register",
        json={"email": "hkaim1st@gmail.com", "password": "securepass", "full_name": "Owner"},
    )
    login = client.post(
        "/api/auth/login",
        data={"username": "hkaim1st@gmail.com", "password": "securepass"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    res = client.get("/api/settings/llm", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["is_platform_owner"] is True
    assert data["serpapi_platform_available"] is True
    assert data["serpapi_configured"] is False


def test_settings_api_non_owner_no_platform_flags(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.platform_owner.settings.platform_owner_email",
        "hkaim1st@gmail.com",
    )
    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.serpapi_key", "platform-serp"
    )

    client.post(
        "/api/auth/register",
        json={"email": "user2@example.com", "password": "securepass", "full_name": "U"},
    )
    login = client.post(
        "/api/auth/login",
        data={"username": "user2@example.com", "password": "securepass"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    res = client.get("/api/settings/llm", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["is_platform_owner"] is False
    assert data["serpapi_platform_available"] is False
