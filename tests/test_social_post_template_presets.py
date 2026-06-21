"""Social post template presets API tests."""

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

DEFAULT_STORE = {
    "version": 1,
    "activePresetId": "preset-1",
    "presets": [
        {
            "id": "preset-1",
            "name": "Default",
            "settings": {
                "templateVariant": "pro-1",
                "headlineFontSize": 56,
                "headlineColor": "#ffffff",
            },
            "updatedAt": 1,
        }
    ],
}


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
        email="templates@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Template Tester",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def test_get_template_presets_empty(client, auth_headers):
    response = client.get("/api/social-post/template-presets", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["store"] is None


def test_save_and_get_template_presets(client, auth_headers):
    save = client.put(
        "/api/social-post/template-presets",
        headers=auth_headers,
        json={"store": DEFAULT_STORE},
    )
    assert save.status_code == 200
    assert save.json()["store"]["activePresetId"] == "preset-1"

    get = client.get("/api/social-post/template-presets", headers=auth_headers)
    assert get.status_code == 200
    assert get.json()["store"]["presets"][0]["name"] == "Default"
    assert get.json()["updated_at"] is not None


def test_save_template_presets_requires_auth(client):
    response = client.put(
        "/api/social-post/template-presets",
        json={"store": DEFAULT_STORE},
    )
    assert response.status_code == 401


def test_save_template_presets_rejects_invalid_active_id(client, auth_headers):
    bad = {
        **DEFAULT_STORE,
        "activePresetId": "missing-id",
    }
    response = client.put(
        "/api/social-post/template-presets",
        headers=auth_headers,
        json={"store": bad},
    )
    assert response.status_code == 400
