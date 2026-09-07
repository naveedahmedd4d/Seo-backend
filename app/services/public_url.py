from typing import Optional

from fastapi import Request

from app.config import settings


def resolve_public_base_url(request: Optional[Request] = None) -> str:
    """
    Public API base URL used for OAuth callbacks and externally reachable assets.

    Priority:
    1. Derive from the incoming HTTP request (Host / X-Forwarded-* headers)
    2. Fall back to API_PUBLIC_URL from settings
    """
    if request is not None:
        forwarded_proto = request.headers.get("x-forwarded-proto")
        forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if forwarded_host:
            scheme = (forwarded_proto or request.url.scheme).split(",")[0].strip()
            host = forwarded_host.split(",")[0].strip()
            return f"{scheme}://{host}".rstrip("/")
        return str(request.base_url).rstrip("/")
    return settings.api_public_url.rstrip("/")


def build_oauth_redirect_uri(platform: str, request: Optional[Request] = None) -> str:
    """OAuth callback URL for a platform, respecting env overrides."""
    env_map = {
        "pinterest": settings.pinterest_redirect_uri,
        "x": settings.x_redirect_uri,
        "mastodon": settings.mastodon_redirect_uri,
        "blogger": settings.blogger_redirect_uri,
        "reddit": settings.reddit_redirect_uri,
        "linkedin": settings.linkedin_redirect_uri,
        "facebook": settings.facebook_redirect_uri,
        "instagram": settings.instagram_redirect_uri,
    }
    override = env_map.get(platform)
    if override:
        return override.strip()
    # OAuth providers reject any mismatch between the redirect_uri shown on the
    # setup screen, the one sent in the authorize request, and the one on the
    # callback. When API_PUBLIC_URL is explicitly configured, use it as the
    # single source of truth instead of per-request Host headers (which can
    # differ behind proxies or when the API is reachable via several domains).
    configured = settings.api_public_url.rstrip("/")
    if configured and configured != "http://localhost:8000":
        return f"{configured}/api/social-accounts/{platform}/callback"
    base = resolve_public_base_url(request)
    return f"{base}/api/social-accounts/{platform}/callback"
