"""Connection test-post API tests."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.social_account import SocialAccount
from app.models.user import User
from app.schemas.social_post import SocialPostPublishResultItem
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
    user = User(
        email="testpost@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Test Poster",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}, user


def test_test_post_requires_connected_account(client, auth_headers):
    headers, _user = auth_headers
    response = client.post("/api/social-accounts/x/test-post", headers=headers, json={})
    assert response.status_code == 400
    assert "Connect" in response.json()["detail"]


def test_test_post_unknown_platform(client, auth_headers):
    headers, _user = auth_headers
    response = client.post(
        "/api/social-accounts/not_a_real_platform/test-post",
        headers=headers,
        json={},
    )
    assert response.status_code == 404


def test_test_post_success_for_connected_devto(client, auth_headers, db_session):
    headers, user = auth_headers
    db_session.add(
        SocialAccount(
            user_id=user.id,
            platform="devto",
            account_label="tester",
            access_token="fake-key",
        )
    )
    db_session.commit()

    fake = SocialPostPublishResultItem(
        platform="devto",
        success=True,
        external_id="123",
        external_url="https://dev.to/tester/connection-test",
        error=None,
    )
    with patch(
        "app.services.social_account_test_post_service.publish_to_platform",
        new_callable=AsyncMock,
        return_value=fake,
    ) as mock_publish:
        response = client.post(
            "/api/social-accounts/devto/test-post",
            headers=headers,
            json={},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["platform"] == "devto"
    assert data["external_url"] == "https://dev.to/tester/connection-test"
    mock_publish.assert_awaited_once()
