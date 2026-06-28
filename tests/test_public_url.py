"""Tests for dynamic public URL resolution."""

from starlette.requests import Request

from app.services.public_url import build_oauth_redirect_uri, resolve_public_base_url
from app.services.social_media_storage import public_image_url


def _make_request(
    *,
    host: str = "localhost:8000",
    scheme: str = "http",
    forwarded_proto: str | None = None,
    forwarded_host: str | None = None,
) -> Request:
    headers = [(b"host", host.encode())]
    if forwarded_proto:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    if forwarded_host:
        headers.append((b"x-forwarded-host", forwarded_host.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "scheme": scheme,
        "server": (host.split(":")[0], int(host.split(":")[1]) if ":" in host else 80),
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def test_resolve_public_base_url_from_host():
    request = _make_request(host="api.example.com")
    assert resolve_public_base_url(request) == "http://api.example.com"


def test_resolve_public_base_url_from_forwarded_headers():
    request = _make_request(
        host="internal:8000",
        forwarded_proto="https",
        forwarded_host="api.production.com",
    )
    assert resolve_public_base_url(request) == "https://api.production.com"


def test_build_oauth_redirect_uri_uses_request_domain():
    request = _make_request(
        forwarded_proto="https",
        forwarded_host="api.myapp.io",
    )
    uri = build_oauth_redirect_uri("blogger", request)
    assert uri == "https://api.myapp.io/api/social-accounts/blogger/callback"


def test_public_image_url_uses_request_host():
    request = _make_request(
        forwarded_proto="https",
        forwarded_host="seo-backend-xgzu.onrender.com",
    )
    url = public_image_url("abc123.png", request)
    assert url == "https://seo-backend-xgzu.onrender.com/api/social-post/media/abc123.png"


def test_public_image_url_falls_back_to_settings_without_request(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "api_public_url", "https://api.test")
    url = public_image_url("file.png")
    assert url == "https://api.test/api/social-post/media/file.png"
