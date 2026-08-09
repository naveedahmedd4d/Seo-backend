"""Unit tests for WordPress post update helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.wordpress_site import WordPressSite
from app.services.wordpress_service import encrypt_api_key, get_wp_post, update_wp_post


@pytest.mark.asyncio
async def test_get_wp_post_success():
    site = WordPressSite(
        user_id=1,
        name="Test",
        url="https://example.com",
        username="admin",
        api_key=encrypt_api_key("secret"),
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": 5,
        "status": "publish",
        "link": "https://example.com/post/",
        "title": {"rendered": "Hello"},
        "content": {"rendered": "<p>Body</p>"},
        "meta": {"rank_math_description": "Desc"},
    }

    with patch("app.services.wordpress_service.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = mock_response
        client_cls.return_value = client

        result = await get_wp_post(site, 5, "post")

    assert result["success"] is True
    assert result["status"] == "publish"


@pytest.mark.asyncio
async def test_update_wp_post_sends_meta():
    site = WordPressSite(
        user_id=1,
        name="Test",
        url="https://example.com",
        username="admin",
        api_key=encrypt_api_key("secret"),
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": 5, "link": "https://example.com/post/", "status": "publish"}

    with patch("app.services.wordpress_service.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = mock_response
        client_cls.return_value = client

        result = await update_wp_post(
            site,
            5,
            "post",
            {"meta": {"rank_math_robots": ["index"], "rank_math_description": "New meta"}},
        )

    assert result["success"] is True
    client.post.assert_called_once()
