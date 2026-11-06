"""Hashnode GraphQL client uses the current endpoint and clear auth errors."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.hashnode_service import HASHNODE_GQL_URL, _auth_header, _graphql, fetch_user


def test_hashnode_endpoint_is_current_beta_host():
    assert HASHNODE_GQL_URL == "https://gql-beta.hashnode.com"


def test_auth_header_adds_bearer_once():
    assert _auth_header("abc123") == "Bearer abc123"
    assert _auth_header("Bearer abc123") == "Bearer abc123"
    assert _auth_header("bearer abc123") == "bearer abc123"


@pytest.mark.asyncio
async def test_graphql_rejects_html_announcement_body():
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "text/html; charset=utf-8"}
    response.text = "<!DOCTYPE html><html>announcement</html>"
    response.json.side_effect = ValueError("should not parse html")

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = AsyncMock(return_value=response)

    with patch("app.services.hashnode_service.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="non-JSON"):
            await _graphql("token", "{ me { id } }")


@pytest.mark.asyncio
async def test_fetch_user_uses_me_payload():
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.text = '{"data":{"me":{"id":"1","username":"naveed","name":"Naveed"}}}'
    response.json.return_value = {
        "data": {"me": {"id": "1", "username": "naveed", "name": "Naveed"}}
    }

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = AsyncMock(return_value=response)

    with patch("app.services.hashnode_service.httpx.AsyncClient", return_value=mock_client):
        me = await fetch_user("pat-token")

    assert me["username"] == "naveed"
    called_url = mock_client.post.await_args.args[0]
    assert called_url == "https://gql-beta.hashnode.com"
