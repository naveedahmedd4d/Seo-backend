"""Upload-Post unified social publishing client."""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import settings
from app.schemas.social_post import SocialPostPublishResultItem
from app.services.http_ssl import httpx_verify

UPLOAD_POST_PLATFORMS = frozenset(
    {
        "instagram",
        "linkedin",
        "facebook",
        "x",
        "threads",
        "pinterest",
        "bluesky",
        "mastodon",
        "tiktok",
        "telegram",
        "discord",
        "google_business",
        "wordpress",
    }
)


def upload_post_configured() -> bool:
    return bool(settings.upload_post_api_key and settings.upload_post_user)


def supports_upload_post(platform: str) -> bool:
    return platform in UPLOAD_POST_PLATFORMS


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Apikey {settings.upload_post_api_key}"}


def _facebook_page_id() -> str | None:
    if settings.upload_post_facebook_page_id:
        return settings.upload_post_facebook_page_id
    user = (settings.upload_post_user or "").strip()
    if user.isdigit():
        return user
    return None



def build_form_payload(
    *,
    user: str,
    platform: str,
    title: Optional[str],
    description: Optional[str],
    image_url: Optional[str],
    extra: dict[str, str],
) -> dict[str, Any]:
    """Build the Upload-Post form body.

    Must be a dict, with lists for repeated keys. httpx 0.27 routes a
    list-of-tuples `data=` through its sync transport, which an AsyncClient
    rejects outright ("Attempted to send an sync request with an AsyncClient
    instance") -- so the shape here is load-bearing, not a style choice.
    """
    payload: dict[str, Any] = {
        "user": user,
        "platform[]": [platform],
        "async_upload": "true",
    }
    if title:
        payload["title"] = title
    if description:
        payload["description"] = description
    if image_url:
        payload["photos[]"] = [image_url]
    payload.update(extra)
    return payload


async def publish_photo_via_upload_post(
    *,
    platform: str,
    title: str,
    image_url: str | None,
    description: str | None = None,
    platform_options: dict | None = None,
) -> SocialPostPublishResultItem:
    """Publish a photo (or caption-only when no image) through Upload-Post."""
    platform_options = platform_options or {}
    if not upload_post_configured():
        return SocialPostPublishResultItem(
            platform=platform,
            success=False,
            error="Upload-Post is not configured (UPLOAD_POST_API_KEY / UPLOAD_POST_USER)",
        )
    if not supports_upload_post(platform):
        return SocialPostPublishResultItem(
            platform=platform,
            success=False,
            error=f"Platform {platform} is not supported by Upload-Post",
        )

    base = settings.upload_post_base_url.rstrip("/")

    extra: dict[str, str] = {}
    if platform == "facebook":
        page_id = (
            platform_options.get("facebook_page_id")
            or platform_options.get("page_id")
            or _facebook_page_id()
        )
        if page_id:
            extra["facebook_page_id"] = str(page_id)

    if platform == "pinterest":
        board_id = platform_options.get("board_id") or platform_options.get("pinterest_board_id")
        if board_id:
            extra["pinterest_board_id"] = str(board_id)

    data = build_form_payload(
        user=str(settings.upload_post_user),
        platform=platform,
        title=title,
        description=description,
        image_url=image_url,
        extra=extra,
    )

    endpoint = f"{base}/upload_photos" if image_url else f"{base}/upload_text"
    if not image_url and not title:
        return SocialPostPublishResultItem(
            platform=platform,
            success=False,
            error="Title/caption is required when publishing without an image",
        )

    try:
        async with httpx.AsyncClient(timeout=90.0, verify=httpx_verify()) as client:
            response = await client.post(endpoint, headers=_auth_headers(), data=data)
        payload: Any
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text[:500]}

        if response.status_code >= 400 or (
            isinstance(payload, dict) and payload.get("success") is False
        ):
            message = None
            if isinstance(payload, dict):
                message = payload.get("message") or payload.get("error")
            return SocialPostPublishResultItem(
                platform=platform,
                success=False,
                error=str(message or f"Upload-Post HTTP {response.status_code}"),
            )

        request_id = None
        external_url = None
        if isinstance(payload, dict):
            request_id = payload.get("request_id") or payload.get("job_id")
            results = payload.get("results") or payload.get("platforms") or {}
            if isinstance(results, dict):
                platform_result = results.get(platform) or {}
                if isinstance(platform_result, dict):
                    external_url = platform_result.get("url") or platform_result.get("post_url")
            external_url = external_url or payload.get("url") or payload.get("post_url")

        return SocialPostPublishResultItem(
            platform=platform,
            success=True,
            external_url=external_url,
            external_id=str(request_id) if request_id else None,
        )
    except Exception as exc:
        return SocialPostPublishResultItem(
            platform=platform,
            success=False,
            error=f"Upload-Post request failed: {exc}",
        )
