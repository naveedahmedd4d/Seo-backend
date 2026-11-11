"""Settings and daily run for free IT/AI news topics."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.news_topics import (
    NewsTopicsCronResponse,
    NewsTopicsSettingsResponse,
    NewsTopicsSettingsUpdate,
)
from app.services import news_topics_service as svc
from app.services.llm_settings_service import get_or_create_settings

router = APIRouter()


def _response(row) -> NewsTopicsSettingsResponse:
    return NewsTopicsSettingsResponse(**svc.settings_payload(row))


@router.get("", response_model=NewsTopicsSettingsResponse)
async def get_news_topics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = get_or_create_settings(db, current_user.id)
    return _response(row)


@router.put("", response_model=NewsTopicsSettingsResponse)
async def save_news_topics(
    body: NewsTopicsSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    fields = body.model_fields_set
    clear_site = body.clear_wordpress_site or (
        "wordpress_site_id" in fields and body.wordpress_site_id is None
    )
    try:
        row = svc.update_news_settings(
            db,
            current_user.id,
            enabled_sources=body.enabled_sources if "enabled_sources" in fields else None,
            daily_blog_enabled=body.daily_blog_enabled if "daily_blog_enabled" in fields else None,
            wordpress_site_id=None if clear_site else body.wordpress_site_id,
            clear_wordpress_site=clear_site,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _response(row)


@router.post("/fetch", response_model=NewsTopicsSettingsResponse)
async def fetch_news_topics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rank today's stories, let the user's model pick 3, and publish one when daily blog is on."""
    try:
        payload = await svc.run_news_topics_for_user(db, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch topics: {exc}",
        ) from exc
    return NewsTopicsSettingsResponse(**payload)


@router.post("/cron/run-due", response_model=NewsTopicsCronResponse)
async def cron_run_due(
    force: bool = Query(default=False),
    authorization: Optional[str] = Header(default=None),
):
    """Hourly scheduler entry. Auth: Bearer WEEKLY_CRON_SECRET. Each user publishes at most once a day."""
    secret = (settings.weekly_cron_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WEEKLY_CRON_SECRET is not configured",
        )
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token or token != secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid cron secret")
    try:
        ran = await svc.run_due_news_topics(force=force)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"news topics cron failed: {type(exc).__name__}: {exc}",
        ) from exc
    return NewsTopicsCronResponse(ran=ran, force=force)
