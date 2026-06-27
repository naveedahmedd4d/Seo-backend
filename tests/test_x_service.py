"""Tests for X media upload and tweet creation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import x_service


@pytest.mark.asyncio
async def test_upload_media_returns_media_id():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"media_id_string": "12345"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.x_service.httpx.AsyncClient", return_value=mock_client):
        media_id = await x_service.upload_media("token", b"png-bytes")

    assert media_id == "12345"
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_create_tweet_with_media_ids():
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"data": {"id": "999"}}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.x_service.httpx.AsyncClient", return_value=mock_client):
        data = await x_service.create_tweet("token", "Hello", media_ids=["12345"])

    assert data["id"] == "999"
    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["json"]["media"] == {"media_ids": ["12345"]}


def test_x_scopes_include_media_write():
    assert "media.write" in x_service.X_SCOPES
