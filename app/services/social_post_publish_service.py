"""Orchestrates history-first social post publishing."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.social_post_history import SocialPostHistory
from app.models.social_post_publication import SocialPostPublication
from app.models.social_account import SocialAccount
from app.models.content_analytics import SocialPublicationAttempt
from app.schemas.social_post import SocialPostPublishResultItem, SocialPostPublishPlatformOptions
from app.services.social_media_storage import public_image_url, resolve_media_path, save_png_base64
from app.services.social_post_copy_service import publish_context_from_history
from app.services.social_post_platform_publish import download_image_bytes, publish_to_platform


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

    results: list[SocialPostPublishResultItem] = []
    for platform in platforms:
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
        results.append(result)
    return results


def record_publications(
    db: Session,
    user_id: int,
    history_id: int,
    results: list[SocialPostPublishResultItem],
) -> None:
    now = datetime.now(timezone.utc)
    for item in results:
        account = (
            db.query(SocialAccount)
            .filter(SocialAccount.user_id == user_id, SocialAccount.platform == item.platform)
            .first()
        )
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
            existing.social_account_id = account.id if account else None
            publication = existing
        else:
            publication = SocialPostPublication(
                    user_id=user_id,
                    history_id=history_id,
                    social_account_id=account.id if account else None,
                    platform=item.platform,
                    status="success" if item.success else "failed",
                    external_id=item.external_id,
                    external_url=item.external_url,
                    error=item.error,
                    published_at=now,
                )
            db.add(publication)
        db.flush()
        db.add(SocialPublicationAttempt(
            user_id=user_id,
            history_id=history_id,
            social_account_id=account.id if account else None,
            publication_id=publication.id,
            platform=item.platform,
            outcome="success" if item.success else "failed",
            external_id=item.external_id,
            error_message=item.error,
            attempted_at=now,
        ))
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
