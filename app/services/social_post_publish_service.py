"""Orchestrates history-first social post publishing."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import settings
from app.models.social_post_history import SocialPostHistory
from app.models.social_post_publication import SocialPostPublication
from app.schemas.social_post import SocialPostPublishResultItem, SocialPostPublishPlatformOptions
from app.services.social_media_storage import public_image_url, resolve_media_path, save_png_base64
from app.services.social_post_copy_service import PublishCopyContext, build_platform_copy, publish_context_from_history
from app.services.social_post_platform_publish import download_image_bytes, publish_to_platform
from app.services.upload_post_service import (
    UPLOAD_POST_PLATFORMS,
    is_upload_post_configured,
    is_upload_post_platform,
    upload_photos,
)


class HistoryNotFoundError(Exception):
    pass


class MediaRequiredError(Exception):
    pass


def load_history_post(db: Session, user_id: int, history_id: int) -> SocialPostHistory:
    history = (
        db.query(SocialPostHistory)
        .filter(SocialPostHistory.id == history_id, SocialPostHistory.user_id == user_id)
        .first()
    )
    if not history:
        raise HistoryNotFoundError(f"Social post history {history_id} not found")
    return history


def ensure_media_saved(
    db: Session,
    history: SocialPostHistory,
    image_base64: Optional[str] = None,
    *,
    request: Optional[Request] = None,
) -> tuple[str, str]:
    """Ensure PNG exists on disk; return (filename, public_url)."""
    if history.media_filename:
        try:
            resolve_media_path(history.media_filename)
            url = public_image_url(history.media_filename, request)
            if history.publish_status == "draft":
                history.publish_status = "media_ready"
                db.commit()
            return history.media_filename, url
        except FileNotFoundError:
            history.media_filename = None

    if not image_base64:
        raise MediaRequiredError("image_base64 is required when media has not been saved yet")

    filename = save_png_base64(image_base64)
    history.media_filename = filename
    history.publish_status = "media_ready"
    db.commit()
    db.refresh(history)
    return filename, public_image_url(filename, request)


def build_publish_context(
    history: SocialPostHistory,
    *,
    request: Optional[Request] = None,
) -> dict[str, Any]:
    ctx = publish_context_from_history(history)
    image_url = (
        public_image_url(history.media_filename, request) if history.media_filename else None
    )
    return {
        "copy_context": ctx,
        "source_url": ctx.source_url,
        "image_url": image_url,
    }


def _platform_options_dict(
    platform: str,
    platform_options: Optional[SocialPostPublishPlatformOptions],
) -> dict:
    if not platform_options:
        return {}
    mapping = {
        "pinterest": platform_options.pinterest,
        "blogger": platform_options.blogger,
        "reddit": platform_options.reddit,
        "facebook": platform_options.facebook,
        "instagram": platform_options.instagram,
    }
    opts = mapping.get(platform)
    return opts if isinstance(opts, dict) else {}


def _upload_post_title_for_platform(platform: str, ctx: PublishCopyContext) -> str:
    copy = build_platform_copy(platform, ctx)
    if platform == "instagram":
        return str(copy.get("caption") or ctx.headline or "")
    if platform == "facebook":
        return str(copy.get("message") or ctx.headline or "")
    if platform == "x":
        return str(copy.get("text") or ctx.headline or "")
    if platform == "pinterest":
        return str(copy.get("title") or ctx.headline or "")
    return ctx.headline or ""


def _upload_post_description(ctx: PublishCopyContext, platforms: list[str]) -> str | None:
    """Shared description field used by Facebook / Pinterest on Upload-Post."""
    if "pinterest" in platforms:
        copy = build_platform_copy("pinterest", ctx)
        desc = copy.get("description")
        if desc:
            return str(desc)
    if "facebook" in platforms:
        story = (ctx.story_caption or ctx.short_description or "").strip()
        return story or None
    return (ctx.story_caption or ctx.short_description or "").strip() or None


async def _publish_via_upload_post(
    platforms: list[str],
    ctx: PublishCopyContext,
    image_bytes: bytes | None,
    platform_options: Optional[SocialPostPublishPlatformOptions],
) -> list[SocialPostPublishResultItem]:
    if not is_upload_post_configured():
        return [
            SocialPostPublishResultItem(
                platform=p,
                success=False,
                error="UPLOAD_POST_API_KEY is not configured",
            )
            for p in platforms
        ]
    if not image_bytes:
        return [
            SocialPostPublishResultItem(
                platform=p,
                success=False,
                error="Image is required to publish via Upload-Post",
            )
            for p in platforms
        ]

    platform_titles = {
        p: _upload_post_title_for_platform(p, ctx) for p in platforms
    }
    title = platform_titles.get(platforms[0]) or ctx.headline or " "
    description = _upload_post_description(ctx, platforms)

    pinterest_opts = _platform_options_dict("pinterest", platform_options)
    facebook_opts = _platform_options_dict("facebook", platform_options)
    board_id = (
        str(pinterest_opts.get("board_id") or "").strip()
        or (settings.upload_post_pinterest_board_id or "").strip()
        or None
    )
    page_id = (
        str(facebook_opts.get("page_id") or "").strip()
        or (settings.upload_post_facebook_page_id or "").strip()
        or None
    )

    return await upload_photos(
        platforms,
        image_bytes,
        title=title,
        description=description,
        platform_titles=platform_titles,
        pinterest_board_id=board_id,
        facebook_page_id=page_id,
    )


async def publish_to_platforms(
    db: Session,
    user_id: int,
    history: SocialPostHistory,
    platforms: list[str],
    *,
    platform_options: Optional[SocialPostPublishPlatformOptions] = None,
    request: Optional[Request] = None,
) -> list[SocialPostPublishResultItem]:
    context = build_publish_context(history, request=request)
    ctx = context["copy_context"]
    image_url = context["image_url"]
    image_bytes: bytes | None = None
    if history.media_filename:
        try:
            path = resolve_media_path(history.media_filename)
            image_bytes = Path(path).read_bytes()
        except FileNotFoundError:
            image_bytes = None
        if image_bytes is None and image_url:
            try:
                image_bytes = await download_image_bytes(image_url)
            except Exception:
                image_bytes = None

    upload_post_platforms = [
        p for p in platforms if is_upload_post_configured() and is_upload_post_platform(p)
    ]
    # Preserve original request order in final results
    upload_set = set(upload_post_platforms)
    native_platforms = [p for p in platforms if p not in upload_set]

    results_by_platform: dict[str, SocialPostPublishResultItem] = {}

    if upload_post_platforms:
        up_results = await _publish_via_upload_post(
            upload_post_platforms,
            ctx,
            image_bytes,
            platform_options,
        )
        for item in up_results:
            results_by_platform[item.platform] = item

    for platform in native_platforms:
        # Guard: if Upload-Post is configured, never fall back to native for migrated IDs
        if is_upload_post_configured() and platform in UPLOAD_POST_PLATFORMS:
            results_by_platform[platform] = SocialPostPublishResultItem(
                platform=platform,
                success=False,
                error="Platform must be published via Upload-Post",
            )
            continue
        opts = _platform_options_dict(platform, platform_options)
        result = await publish_to_platform(
            db,
            user_id,
            platform,
            ctx=ctx,
            image_url=image_url,
            image_bytes=image_bytes if platform == "x" else None,
            platform_options=opts,
        )
        results_by_platform[result.platform] = result

    return [
        results_by_platform.get(
            p,
            SocialPostPublishResultItem(
                platform=p, success=False, error="No publish result"
            ),
        )
        for p in platforms
    ]


def record_publications(
    db: Session,
    user_id: int,
    history_id: int,
    results: list[SocialPostPublishResultItem],
) -> None:
    now = datetime.now(timezone.utc)
    for item in results:
        existing = (
            db.query(SocialPostPublication)
            .filter(
                SocialPostPublication.history_id == history_id,
                SocialPostPublication.platform == item.platform,
            )
            .first()
        )
        if existing:
            existing.status = "success" if item.success else "failed"
            existing.external_id = item.external_id
            existing.external_url = item.external_url
            existing.error = item.error
            existing.published_at = now
        else:
            db.add(
                SocialPostPublication(
                    user_id=user_id,
                    history_id=history_id,
                    platform=item.platform,
                    status="success" if item.success else "failed",
                    external_id=item.external_id,
                    external_url=item.external_url,
                    error=item.error,
                    published_at=now,
                )
            )
    db.commit()


def finalize_history_status(
    db: Session,
    history: SocialPostHistory,
    results: list[SocialPostPublishResultItem],
) -> str:
    successes = sum(1 for r in results if r.success)
    total = len(results)
    if total == 0:
        status = history.publish_status or "draft"
    elif successes == total:
        status = "published"
    elif successes == 0:
        status = "failed"
    else:
        status = "partial"

    history.publish_status = status
    if successes > 0 and not history.published_at:
        history.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(history)
    return status


async def publish_history_post(
    db: Session,
    user_id: int,
    history_id: int,
    platforms: list[str],
    *,
    image_base64: Optional[str] = None,
    platform_options: Optional[SocialPostPublishPlatformOptions] = None,
    request: Optional[Request] = None,
) -> tuple[list[SocialPostPublishResultItem], SocialPostHistory, str | None]:
    history = load_history_post(db, user_id, history_id)
    history.publish_status = "publishing"
    db.commit()

    filename, media_url = ensure_media_saved(db, history, image_base64, request=request)
    db.refresh(history)

    results = await publish_to_platforms(
        db,
        user_id,
        history,
        platforms,
        platform_options=platform_options,
        request=request,
    )
    record_publications(db, user_id, history_id, results)
    status = finalize_history_status(db, history, results)
    db.refresh(history)
    return results, history, media_url
