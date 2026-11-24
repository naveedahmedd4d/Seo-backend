"""Tests for X media upload and tweet creation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import x_service


@pytest.mark.asyncio
async def test_upload_media_uses_v2_init_append_finalize():
    init_response = MagicMock()
    init_response.status_code = 200
    init_response.content = b'{"data":{"id":"12345"}}'
    init_response.json.return_value = {"data": {"id": "12345"}}

    append_response = MagicMock()
    append_response.status_code = 204
    append_response.content = b""
    append_response.text = ""

    finalize_response = MagicMock()
    finalize_response.status_code = 200
    finalize_response.content = b'{"data":{"id":"12345"}}'
    finalize_response.json.return_value = {"data": {"id": "12345"}}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[init_response, append_response, finalize_response])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.x_service.httpx.AsyncClient", return_value=mock_client):
        media_id = await x_service.upload_media("token", b"png-bytes")

    assert media_id == "12345"
    assert mock_client.post.await_count == 3
    first_url = mock_client.post.await_args_list[0].args[0]
    assert first_url == "https://api.x.com/2/media/upload"


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


def test_x_scopes_omit_media_write_at_connect():
    # Free/Basic apps hang on authorize when media.write is requested.
    assert "tweet.write" in x_service.X_SCOPES
    assert "offline.access" in x_service.X_SCOPES
    assert "media.write" not in x_service.X_SCOPES
    assert x_service.X_MEDIA_UPLOAD_URL == "https://api.x.com/2/media/upload"


def test_x_oauth_error_message_maps_expired_code():
    response = MagicMock()
    response.status_code = 400
    response.json.return_value = {
        "error": "invalid_grant",
        "error_description": "Value passed for the authorization code was invalid.",
    }
    response.text = ""
    message = x_service._x_oauth_error_message(response)
    assert "expired" in message.lower() or "already used" in message.lower()
