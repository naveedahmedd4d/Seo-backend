"""Tests for per-user LLM settings resolver and API."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.llm_settings_service import (
    LlmCredentials,
    resolve_llm_credentials,
    upsert_settings,
)
from app.services.openrouter_service import (
    generate_blog_content_with_groq,
    get_llm_context,
    reset_llm_context,
    set_llm_context,
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


@pytest.fixture()
def auth_headers(client):
    client.post(
        "/api/auth/register",
        json={"email": "llm@example.com", "password": "securepass", "full_name": "LLM User"},
    )
    login = client.post(
        "/api/auth/login",
        data={"username": "llm@example.com", "password": "securepass"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_resolve_prefers_user_openrouter_key(db_session, monkeypatch):
    from app.models.user import User
    from app.services.auth_service import get_password_hash

    user = User(email="a@b.com", password_hash=get_password_hash("x"), full_name="A")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    monkeypatch.setattr("app.services.llm_settings_service.settings.openrouter_api_key", "platform-or")
    monkeypatch.setattr("app.services.llm_settings_service.settings.groq_api_key", "platform-groq")

    upsert_settings(db_session, user.id, openrouter_api_key="sk-user-openrouter-key")
    creds = resolve_llm_credentials(db_session, user.id)
    assert creds.provider == "openrouter"
    assert creds.api_key == "sk-user-openrouter-key"
    assert creds.source == "user"


def test_resolve_falls_back_to_platform(db_session, monkeypatch):
    from app.models.user import User
    from app.services.auth_service import get_password_hash

    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.platform_owner_email", "owner@example.com"
    )
    monkeypatch.setattr(
        "app.services.platform_owner.settings.platform_owner_email", "owner@example.com"
    )
    user = User(email="owner@example.com", password_hash=get_password_hash("x"), full_name="B")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    monkeypatch.setattr("app.services.llm_settings_service.settings.openrouter_api_key", "platform-or-key")
    monkeypatch.setattr("app.services.llm_settings_service.settings.groq_api_key", None)

    creds = resolve_llm_credentials(db_session, user.id)
    assert creds.provider == "openrouter"
    assert creds.api_key == "platform-or-key"
    assert creds.source == "platform"


def test_resolve_model_override(db_session, monkeypatch):
    from app.models.user import User
    from app.services.auth_service import get_password_hash

    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.platform_owner_email", "owner@example.com"
    )
    monkeypatch.setattr(
        "app.services.platform_owner.settings.platform_owner_email", "owner@example.com"
    )
    user = User(email="owner@example.com", password_hash=get_password_hash("x"), full_name="C")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    monkeypatch.setattr("app.services.llm_settings_service.settings.groq_api_key", "g-key")
    monkeypatch.setattr("app.services.llm_settings_service.settings.openrouter_api_key", None)

    upsert_settings(db_session, user.id, preferred_provider="groq")
    creds = resolve_llm_credentials(db_session, user.id, model_override="llama-3.1-8b-instant")
    assert creds.model == "openai/gpt-oss-20b"


def test_resolve_rejects_unknown_model(db_session, monkeypatch):
    from app.models.user import User
    from app.services.auth_service import get_password_hash

    monkeypatch.setattr(
        "app.services.llm_settings_service.settings.platform_owner_email", "owner@example.com"
    )
    monkeypatch.setattr(
        "app.services.platform_owner.settings.platform_owner_email", "owner@example.com"
    )
    user = User(email="owner@example.com", password_hash=get_password_hash("x"), full_name="D")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    monkeypatch.setattr("app.services.llm_settings_service.settings.groq_api_key", "g-key")
    monkeypatch.setattr("app.services.llm_settings_service.settings.openrouter_api_key", None)

    with pytest.raises(ValueError, match="not allowed"):
        resolve_llm_credentials(db_session, user.id, model_override="totally-fake-model")


def test_settings_api_masks_keys(client, auth_headers):
    put = client.put(
        "/api/settings/llm",
        headers=auth_headers,
        json={"openrouter_api_key": "sk-or-v1-abcdefghijklmnop1234"},
    )
    assert put.status_code == 200
    data = put.json()
    assert data["openrouter_configured"] is True
    assert data["openrouter_hint"] == "1234"
    assert "api_key" not in data or data.get("openrouter_api_key") is None

    get = client.get("/api/settings/llm", headers=auth_headers)
    assert get.status_code == 200
    assert get.json()["openrouter_configured"] is True
    assert get.json()["openrouter_hint"] == "1234"


def test_settings_api_empty_keeps_key(client, auth_headers):
    client.put(
        "/api/settings/llm",
        headers=auth_headers,
        json={"openrouter_api_key": "sk-keep-this-key-zzzz"},
    )
    put = client.put(
        "/api/settings/llm",
        headers=auth_headers,
        json={"preferred_provider": "openrouter", "openrouter_api_key": ""},
    )
    assert put.status_code == 200
    assert put.json()["openrouter_configured"] is True
    assert put.json()["openrouter_hint"] == "zzzz"


def test_settings_api_clear_removes_key(client, auth_headers):
    client.put(
        "/api/settings/llm",
        headers=auth_headers,
        json={"groq_api_key": "gsk-clear-me-aaaa"},
    )
    put = client.put(
        "/api/settings/llm",
        headers=auth_headers,
        json={"clear_groq_api_key": True},
    )
    assert put.status_code == 200
    assert put.json()["groq_configured"] is False
    assert put.json()["groq_hint"] is None


def test_models_allowlist(client, auth_headers):
    res = client.get("/api/settings/llm/models", headers=auth_headers)
    assert res.status_code == 200
    ids = [m["id"] for m in res.json()["models"]]
    assert "default" in ids
    # gemini-2.5-flash was retired by Google (404, "no longer available to new
    # users"); the live replacement it names is asserted below.
    assert "gemini-3.8-flash" in ids
    assert "openrouter/free" in ids
    assert "google/gemma-4-31b-it:free" in ids
    assert "openai/gpt-oss-120b" in ids
    assert "openai/gpt-oss-20b" in ids


def test_resolve_prefers_user_gemini_key(db_session, monkeypatch):
    from app.models.user import User
    from app.services.auth_service import get_password_hash

    user = User(email="gem@b.com", password_hash=get_password_hash("x"), full_name="G")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    monkeypatch.setattr("app.services.llm_settings_service.settings.gemini_api_key", "platform-gem")
    monkeypatch.setattr("app.services.llm_settings_service.settings.openrouter_api_key", "platform-or")
    monkeypatch.setattr("app.services.llm_settings_service.settings.groq_api_key", "platform-groq")

    upsert_settings(db_session, user.id, gemini_api_key="user-gemini-key", preferred_provider="default")
    creds = resolve_llm_credentials(db_session, user.id)
    assert creds.provider == "gemini"
    assert creds.api_key == "user-gemini-key"
    assert creds.source == "user"


def test_manual_fallback_order(db_session, monkeypatch):
    from app.models.user import User
    from app.services.auth_service import get_password_hash

    user = User(email="order@b.com", password_hash=get_password_hash("x"), full_name="O")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    upsert_settings(
        db_session,
        user.id,
        openrouter_api_key="or-key",
        groq_api_key="groq-key",
        preferred_provider="default",
        fallback_order=["groq", "openrouter", "gemini"],
    )
    creds = resolve_llm_credentials(db_session, user.id)
    assert creds.provider == "groq"
    assert creds.api_key == "groq-key"


@pytest.mark.asyncio
async def test_context_uses_injected_credentials(monkeypatch):
    called = {}

    async def fake_openrouter(**kwargs):
        called["api_key"] = kwargs.get("api_key_override")
        called["model"] = kwargs.get("model")
        return "ok"

    monkeypatch.setattr(
        "app.services.openrouter_service._openrouter_chat_completion",
        fake_openrouter,
    )

    creds = LlmCredentials(
        provider="openrouter",
        api_key="user-key-xyz",
        model="openai/gpt-4o-mini",
        source="user",
    )
    token = set_llm_context(creds)
    try:
        assert get_llm_context() is creds
        result = await generate_blog_content_with_groq("hello", max_tokens=10)
        assert result == "ok"
        assert called["api_key"] == "user-key-xyz"
        assert called["model"] == "openai/gpt-4o-mini"
    finally:
        reset_llm_context(token)


# --- Test endpoint honours the caller's model ---------------------------------

DEAD_MODEL_IDS = [
    "gemini-2.5-flash",
    "@cf/meta/infire-llama-3.1-8b-instruct",
    "command-light",
    "meta/llama-3.1-8b-instruct",
    "google/gemma-2-9b-it",
]


def test_allowlist_drops_vendor_confirmed_dead_models(client, auth_headers):
    res = client.get("/api/settings/llm/models", headers=auth_headers)
    assert res.status_code == 200
    ids = {m["id"] for m in res.json()["models"]}
    for dead in DEAD_MODEL_IDS:
        assert dead not in ids, f"{dead} is EOL at the vendor and must not be offered"


def test_allowlist_has_google_named_replacements(client, auth_headers):
    res = client.get("/api/settings/llm/models", headers=auth_headers)
    ids = {m["id"] for m in res.json()["models"]}
    assert "gemini-3.8-flash" in ids
    assert "gemini-3.5-flash-lite" in ids


def _capture_probe(monkeypatch):
    """Record the payload the test endpoint sends to the vendor."""
    sent = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent["url"] = url
            sent["model"] = (json or {}).get("model")
            return _Resp()

    monkeypatch.setattr("app.api.settings_llm.httpx.AsyncClient", _Client)
    return sent


def test_test_endpoint_probes_the_requested_model(client, auth_headers, monkeypatch):
    sent = _capture_probe(monkeypatch)
    res = client.post(
        "/api/settings/llm/test",
        headers=auth_headers,
        json={"provider": "gemini", "api_key": "k-test", "model": "gemini-1.5-flash"},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert sent["model"] == "gemini-1.5-flash"


def test_test_endpoint_without_model_uses_provider_default(client, auth_headers, monkeypatch):
    sent = _capture_probe(monkeypatch)
    res = client.post(
        "/api/settings/llm/test",
        headers=auth_headers,
        json={"provider": "groq", "api_key": "k-test"},
    )
    assert res.status_code == 200
    assert sent["model"] == "openai/gpt-oss-20b"


def test_test_endpoint_rejects_model_from_another_provider(client, auth_headers, monkeypatch):
    _capture_probe(monkeypatch)
    res = client.post(
        "/api/settings/llm/test",
        headers=auth_headers,
        json={"provider": "gemini", "api_key": "k-test", "model": "openai/gpt-oss-20b"},
    )
    assert res.status_code == 400


def test_test_endpoint_reports_the_model_it_probed(client, auth_headers, monkeypatch):
    sent = {}

    class _Resp:
        status_code = 404

        def json(self):
            return {"error": {"message": "models/gemini-1.5-flash is not found"}}

        text = "not found"

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent["model"] = (json or {}).get("model")
            return _Resp()

    monkeypatch.setattr("app.api.settings_llm.httpx.AsyncClient", _Client)
    res = client.post(
        "/api/settings/llm/test",
        headers=auth_headers,
        json={"provider": "gemini", "api_key": "k-test", "model": "gemini-1.5-flash"},
    )
    body = res.json()
    assert body["ok"] is False
    # the message must name the model actually probed, not a provider default
    assert "gemini-1.5-flash" in body["message"]
