"""Tests for Upload-Post client and result mapping."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.social_post import SocialPostPublishResultItem
from app.services import upload_post_service as ups


@pytest.fixture(autouse=True)
def _upload_post_settings(monkeypatch):
    monkeypatch.setattr(ups.settings, "upload_post_api_key", "test-api-key")
    monkeypatch.setattr(ups.settings, "upload_post_user", "trendifypress")
    monkeypatch.setattr(ups.settings, "upload_post_base_url", "https://api.upload-post.com")
    monkeypatch.setattr(ups.settings, "upload_post_pinterest_board_id", None)
    monkeypatch.setattr(ups.settings, "upload_post_facebook_page_id", None)


def test_is_upload_post_configured(monkeypatch):
    monkeypatch.setattr(ups.settings, "upload_post_api_key", "  ")
    assert ups.is_upload_post_configured() is False
    monkeypatch.setattr(ups.settings, "upload_post_api_key", "abc")
    assert ups.is_upload_post_configured() is True


def test_map_platform_results_success_and_failure():
    results = {
        "instagram": {"success": True, "container_id": "ig-1", "url": "https://ig/p/1"},
        "x": {"success": False, "error": "token expired"},
    }
    items = ups.map_platform_results(["instagram", "x"], results)
    assert items[0].success is True
    assert items[0].external_id == "ig-1"
    assert items[0].external_url == "https://ig/p/1"
    assert items[1].success is False
    assert "token expired" in (items[1].error or "")


def test_map_platform_results_request_error():
    items = ups.map_platform_results(
        ["facebook"], None, request_error="Unauthorized"
    )
    assert len(items) == 1
    assert items[0].success is False
    assert items[0].error == "Unauthorized"


def test_build_synthetic_account_responses():
    connected = {
        "instagram": {
            "label": "trendifypress · Upload-Post",
            "raw": {"display_name": "trendifypress"},
            "synthetic_id": -101,
        }
    }
    rows = ups.build_synthetic_account_responses(connected)
    assert len(rows) == 1
    assert rows[0]["id"] == -101
    assert rows[0]["platform"] == "instagram"
    assert rows[0]["metadata"]["managed_by"] == "upload_post"


@pytest.mark.asyncio
async def test_upload_photos_maps_results():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"success":true}'
    mock_response.json.return_value = {
        "success": True,
        "results": {
            "instagram": {"success": True, "container_id": "c1"},
            "x": {"success": True, "publish_id": "t1"},
        },
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=None)

    with patch("app.services.upload_post_service.httpx.Client", return_value=mock_client):
        items = await ups.upload_photos(
            ["instagram", "x"],
            b"fake-png-bytes",
            title="Hello",
            platform_titles={"instagram": "IG caption", "x": "tweet"},
        )

    assert len(items) == 2
    assert all(i.success for i in items)
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Apikey test-api-key"
    multipart = call_kwargs["files"]
    keys = [k for k, _ in multipart]
    assert "platform[]" in keys
    assert "photos[]" in keys
    assert any(k == "user" and v == (None, "trendifypress") for k, v in multipart)

@pytest.mark.asyncio
async def test_upload_photos_http_error():
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.content = b'{"success":false,"message":"Invalid API key"}'
    mock_response.text = '{"success":false,"message":"Invalid API key"}'
    mock_response.json.return_value = {"success": False, "message": "Invalid API key"}

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=None)

    with patch("app.services.upload_post_service.httpx.Client", return_value=mock_client):
        items = await ups.upload_photos(
            ["facebook"],
            b"img",
            title="Hi",
        )

    assert len(items) == 1
    assert items[0].success is False
    assert "Invalid API key" in (items[0].error or "")


@pytest.mark.asyncio
async def test_upload_photos_requires_pinterest_board(monkeypatch):
    monkeypatch.setattr(ups.settings, "upload_post_pinterest_board_id", None)

    # Only pinterest — should fail without calling API
    items = await ups.upload_photos(
        ["pinterest"],
        b"img",
        title="Pin",
        pinterest_board_id=None,
    )
    assert len(items) == 1
    assert items[0].success is False
    assert "pinterest_board_id" in (items[0].error or "")


@pytest.mark.asyncio
async def test_get_connected_platforms():
    profile = {
        "username": "trendifypress",
        "social_accounts": {
            "instagram": {"display_name": "trendifypress"},
            "facebook": {"display_name": "Hammad Khan"},
            "pinterest": "",
            "x": {"username": "xuser"},
            "tiktok": {"display_name": "ignored"},
        },
    }
    with patch.object(ups, "get_profile", new=AsyncMock(return_value=profile)):
        connected = await ups.get_connected_platforms()

    assert set(connected.keys()) == {"instagram", "facebook", "x"}
    assert "pinterest" not in connected
