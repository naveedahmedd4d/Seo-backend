from unittest.mock import MagicMock, patch

import ssl

from app.services.http_ssl import httpx_verify


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
