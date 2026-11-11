"""Resolve and attach a social-card image for weekly Goal 1 auto-publish."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.social_post_history import SocialPostHistory
from app.models.user import User
from app.services.http_ssl import httpx_verify
from app.services.llm_settings_service import resolve_api_key_optional
from app.services.openrouter_service import select_social_background_image
from app.services.serpapi_service import search_images
from app.services.social_media_storage import public_image_url, save_png_base64

logger = logging.getLogger(__name__)

RENDER_TIMEOUT_SECONDS = 45.0
SERP_CANDIDATE_COUNT = 12


def _candidate_image_url(img: dict[str, Any]) -> str:
    for key in ("original", "link", "thumbnail"):
        value = img.get(key)
        if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
            return value.strip()
    return ""


def _fallback_index(n: int) -> int:
    if n <= 0:
        return 0
    return 2 if n >= 3 else 0


async def _search_background_candidates(
    db: Session,
    user: User,
    query: str,
) -> list[dict[str, Any]]:
    serp_key = resolve_api_key_optional(db, user, "serpapi")
    if not serp_key:
        logger.info("Weekly social media: no SerpAPI key for user %s", user.id)
        return []
    try:
        results = await search_images(
            (query or "").strip() or "news",
            num_results=SERP_CANDIDATE_COUNT,
            api_key=serp_key,
        )
    except Exception as exc:
        logger.warning("Weekly social media: SerpAPI search failed: %s", exc)
        return []
    return [r for r in (results or []) if isinstance(r, dict) and _candidate_image_url(r)]


async def _pick_background_url(
    *,
    event_title: str,
    headline: str,
    description: str,
    candidates: list[dict[str, Any]],
) -> Optional[str]:
    if not candidates:
        return None
    try:
        idx = await select_social_background_image(
            event_title,
            headline,
            description,
            candidates,
        )
    except Exception as exc:
        logger.warning("Weekly social media: LLM image pick failed: %s", exc)
        idx = _fallback_index(len(candidates))
    if idx < 0 or idx >= len(candidates):
        idx = _fallback_index(len(candidates))
    return _candidate_image_url(candidates[idx]) or None


def _render_local_fallback_png(*, headline: str, description: str) -> Optional[str]:
    """Pillow card when SerpAPI / remote /api/social-card/render are unavailable."""
    import base64
    from io import BytesIO

    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:
        logger.warning("Weekly social media: Pillow unavailable for fallback card: %s", exc)
        return None

    try:
        width, height = 1080, 1350
        img = Image.new("RGB", (width, height), (18, 24, 38))
        draw = ImageDraw.Draw(img)
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
            body_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        except Exception:
            title_font = ImageFont.load_default()
            body_font = title_font

        margin = 72
        max_width = width - margin * 2
        y = 180

        def _wrap(text: str, font: Any, limit: int) -> list[str]:
            words = (text or "").split()
            if not words:
                return []
            lines: list[str] = []
            current = words[0]
            for word in words[1:]:
                trial = f"{current} {word}"
                if draw.textlength(trial, font=font) <= max_width:
                    current = trial
                else:
                    lines.append(current)
                    current = word
                    if len(lines) >= limit:
                        return lines
            lines.append(current)
            return lines[:limit]

        title_lines = _wrap((headline or "Weekly update").strip(), title_font, 5)
        for line in title_lines:
            draw.text((margin, y), line, fill=(248, 250, 252), font=title_font)
            y += 78
        y += 36
        for line in _wrap((description or "").strip(), body_font, 8):
            draw.text((margin, y), line, fill=(203, 213, 225), font=body_font)
            y += 48

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        logger.warning("Weekly social media: local fallback card failed: %s", exc)
        return None


async def _render_social_card_png(
    *,
    headline: str,
    description: str,
    story: Optional[str],
    hashtags: Optional[list[str]],
    background_image_url: Optional[str],
) -> Optional[str]:
    """Call Next.js /api/social-card/render; return png base64 or None."""
    frontend_url = (settings.frontend_url or "").rstrip("/")
    secret = (settings.infographic_render_secret or "").strip()
    if not frontend_url or not secret:
        logger.info("Weekly social media: card render not configured (FRONTEND_URL / secret)")
        return None
    payload = {
        "headline": headline,
        "description": description or "",
        "story": story or "",
        "hashtags": hashtags or [],
        "background_image_url": background_image_url or "",
    }
    try:
        async with httpx.AsyncClient(verify=httpx_verify(), timeout=RENDER_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{frontend_url}/api/social-card/render",
                json=payload,
                headers={"x-infographic-secret": secret},
            )
        if response.status_code != 200:
            logger.warning(
                "Weekly social media: render returned %s: %s",
                response.status_code,
                response.text[:300],
            )
            return None
        data = response.json()
        if not isinstance(data, dict) or not data.get("ok"):
            logger.warning("Weekly social media: render failed: %s", data)
            return None
        png = data.get("png_base64")
        return png if isinstance(png, str) and png.strip() else None
    except Exception as exc:
        logger.warning("Weekly social media: render call failed: %s", exc)
        return None


async def ensure_weekly_social_media(
    db: Session,
    user: User,
    history: SocialPostHistory,
    *,
    query: str,
    headline: str,
    description: str,
    story: Optional[str] = None,
    hashtags: Optional[list[str]] = None,
    existing_image_url: Optional[str] = None,
) -> Optional[str]:
    """Ensure weekly Goal 1 has a publishable image URL.

    Returns a public image_url (saved card or external URL), or None if nothing
    usable was found. Updates history.media_filename when a card PNG is saved.
    """
    if existing_image_url and existing_image_url.strip().startswith(("http://", "https://")):
        return existing_image_url.strip()

    if history.media_filename:
        try:
            return public_image_url(history.media_filename)
        except Exception:
            history.media_filename = None

    candidates = await _search_background_candidates(db, user, query)
    background_url = await _pick_background_url(
        event_title=query or (history.query or ""),
        headline=headline,
        description=description or "",
        candidates=candidates,
    )

    png_b64 = await _render_social_card_png(
        headline=headline,
        description=description or "",
        story=story,
        hashtags=hashtags,
        background_image_url=background_url,
    )
    if not png_b64:
        png_b64 = _render_local_fallback_png(
            headline=headline or query or "Weekly update",
            description=description or "",
        )
    if png_b64:
        try:
            filename = save_png_base64(png_b64)
            history.media_filename = filename
            if history.publish_status == "draft":
                history.publish_status = "media_ready"
            db.commit()
            db.refresh(history)
            return public_image_url(filename)
        except Exception as exc:
            logger.warning("Weekly social media: failed to save rendered card: %s", exc)

    # Card render unavailable — Facebook can still download a raw photo URL.
    if background_url:
        return background_url
    return None
