"""Publish a one-off connection test to a connected social/backlink platform."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.schemas.social_post import SocialPostPublishPlatformOptions, SocialPostPublishResultItem
from app.services.outreach_platforms import is_outreach_platform
from app.services.public_url import resolve_public_base_url
from app.services.social_account_service import get_user_social_account
from app.services.social_media_storage import public_image_url, save_png_base64
from app.services.social_platform_registry import get_social_platforms
from app.services.social_post_copy_service import PublishCopyContext
from app.services.social_post_platform_publish import publish_to_platform

logger = logging.getLogger(__name__)

# Platforms that need a public image URL (or image bytes) for a meaningful publish.
_IMAGE_PLATFORMS = frozenset(
    {
        "pinterest",
        "facebook",
        "instagram",
        "x",
        "linkedin",
        "mastodon",
        "bluesky",
        "reddit",
    }
)


class TestPostError(Exception):
    """Raised when a connection test cannot be started."""

    def __init__(self, detail: str, *, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _platform_options_dict(
    platform: str,
    platform_options: Optional[SocialPostPublishPlatformOptions],
) -> dict[str, Any]:
    if not platform_options:
        return {}
    mapping = {
        "pinterest": platform_options.pinterest,
        "blogger": platform_options.blogger,
        "reddit": platform_options.reddit,
        "facebook": platform_options.facebook,
        "instagram": platform_options.instagram,
        "tumblr": platform_options.tumblr,
        "wordpress_com": platform_options.wordpress_com,
        "linkedin": platform_options.linkedin,
    }
    opts = mapping.get(platform)
    return opts if isinstance(opts, dict) else {}


def _build_test_card_png_base64(*, headline: str, subtitle: str) -> str:
    """Render a small branded PNG so image-card platforms can be exercised."""
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1080, 1080
    img = Image.new("RGB", (width, height), (15, 23, 42))
    draw = ImageDraw.Draw(img)
    try:
        title_font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 56
        )
        body_font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32
        )
    except Exception:
        title_font = ImageFont.load_default()
        body_font = title_font

    margin = 72
    y = 360
    draw.text((margin, y), headline[:80], fill=(248, 250, 252), font=title_font)
    y += 90
    draw.text((margin, y), subtitle[:120], fill=(148, 163, 184), font=body_font)
    draw.text(
        (margin, height - 120),
        "SEO automation · connection test",
        fill=(94, 234, 212),
        font=body_font,
    )

    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _test_copy_context(*, request: Optional[Request] = None) -> PublishCopyContext:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    base = resolve_public_base_url(request) if request is not None else "https://example.com"
    return PublishCopyContext(
        headline=f"Connection test · {stamp}",
        short_description="Automated connection test from SEO automation. Safe to delete.",
        story_caption=(
            "This is an automated connection test from SEO automation. "
            "If you can see this post, the platform integration is working. "
            "Safe to delete."
        ),
        hashtags=["connectiontest", "seoautomation"],
        source_url=f"{base.rstrip('/')}/connection-test",
    )


async def run_platform_test_post(
    db: Session,
    user_id: int,
    platform: str,
    *,
    platform_options: Optional[SocialPostPublishPlatformOptions] = None,
    request: Optional[Request] = None,
) -> SocialPostPublishResultItem:
    platform_id = (platform or "").strip().lower()
    if not platform_id:
        raise TestPostError("Platform is required")

    registry = {p.id: p for p in get_social_platforms()}
    meta = registry.get(platform_id)
    if meta is None:
        raise TestPostError(f"Unknown platform: {platform_id}", status_code=404)
    if meta.status != "active":
        raise TestPostError(f"{meta.name} is not available for publishing yet")
    if not meta.post_api_available:
        raise TestPostError(f"{meta.name} does not support API publishing")

    account = get_user_social_account(db, user_id, platform_id)
    if account is None:
        raise TestPostError(
            f"Connect {meta.name} before running a test post",
            status_code=400,
        )

    ctx = _test_copy_context(request=request)
    opts = _platform_options_dict(platform_id, platform_options)

    image_url: str | None = None
    image_bytes: bytes | None = None
    needs_image = platform_id in _IMAGE_PLATFORMS or not is_outreach_platform(platform_id)
    # Outreach (article) platforms publish text/HTML only — skip the card image.
    if is_outreach_platform(platform_id):
        needs_image = False

    if needs_image:
        try:
            png_b64 = _build_test_card_png_base64(
                headline="Connection test",
                subtitle=ctx.short_description or "",
            )
            filename = save_png_base64(png_b64)
            image_url = public_image_url(filename, request)
            image_bytes = base64.b64decode(png_b64)
        except Exception as exc:
            logger.exception("Failed to build test card image for %s", platform_id)
            raise TestPostError(f"Could not prepare test media: {exc}") from exc

    result = await publish_to_platform(
        db,
        user_id,
        platform_id,
        ctx=ctx,
        image_url=image_url,
        image_bytes=image_bytes,
        platform_options=opts,
    )
    return result
