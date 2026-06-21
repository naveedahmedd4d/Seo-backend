import base64
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.auth import get_current_user
from app.models.user import User


@pytest.fixture
def authed_client():
    user = User(id=1, email="test@example.com", password_hash="x")

    def override_user():
        return user

    app.dependency_overrides[get_current_user] = override_user
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_fetch_image_rejects_private_urls(authed_client):
    response = authed_client.post(
        "/api/social-post/fetch-image",
        json={"url": "http://127.0.0.1/photo.jpg"},
    )
    assert response.status_code == 400


@patch("app.api.social_post.httpx.AsyncClient")
def test_fetch_image_returns_data_url(mock_client_cls, authed_client):
    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None
    mock_response.headers = {"content-type": "image/png"}
    mock_response.content = b"\x89PNG\r\n\x1a\n" + b"fake"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client_cls.return_value = mock_client

    response = authed_client.post(
        "/api/social-post/fetch-image",
        json={"url": "https://example.com/photo.png"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_type"] == "image/png"
    assert payload["data_url"].startswith("data:image/png;base64,")
    encoded = payload["data_url"].split(",", 1)[1]
    assert base64.b64decode(encoded).startswith(b"\x89PNG")
