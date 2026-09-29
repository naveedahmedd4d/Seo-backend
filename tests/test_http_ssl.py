from unittest.mock import MagicMock, patch

import asyncio
import ssl

import httpx

from app.services.http_ssl import httpx_verify
from app.services.meta_graph_service import graph_get


def test_httpx_verify_combines_certifi_and_os_trust_stores():
    context = MagicMock(spec=ssl.SSLContext)
    context.verify_flags = ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_TRUSTED_FIRST

    with (
        patch("app.services.http_ssl.settings.groq_ssl_verify", True),
        patch("certifi.where", return_value="certifi-ca.pem"),
        patch("app.services.http_ssl.ssl.create_default_context", return_value=context) as create,
    ):
        result = httpx_verify()

    assert result is context
    create.assert_called_once_with(cafile="certifi-ca.pem")
    context.load_default_certs.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
    assert not result.verify_flags & ssl.VERIFY_X509_STRICT
    assert result.verify_flags & ssl.VERIFY_X509_TRUSTED_FIRST


def test_httpx_verify_can_be_disabled_for_local_development():
    with patch("app.services.http_ssl.settings.groq_ssl_verify", False):
        assert httpx_verify() is False


def test_meta_graph_get_keeps_access_token_out_of_url():
    observed_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_request
        observed_request = request
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    with patch("app.services.meta_graph_service.httpx.AsyncClient", side_effect=mock_client):
        asyncio.run(graph_get("me/accounts", "secret-token", params={"limit": 1}))

    assert observed_request is not None
    assert "secret-token" not in str(observed_request.url)
    assert observed_request.headers["Authorization"] == "Bearer secret-token"
