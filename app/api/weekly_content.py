"""Weekly auto-content API: settings, events, social week, CSV keywords, blog week, manual run."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.weekly_content import (
    CalendarEventItem,
    CalendarEventsResponse,
    KeywordCsvUploadResponse,
    KeywordPlanItemResponse,
    KeywordPlanListResponse,
    SocialTopicCreate,
    SocialTopicResponse,
    WeeklyBlogItemResponse,
    WeeklyBlogWeekResponse,
    WeeklyContentSettingsResponse,
    WeeklyContentSettingsUpdate,
    WeeklyCronRunResponse,
    WeeklyReadinessResponse,
    WeeklyRunResponse,
    WeeklySocialItemResponse,
    WeeklySocialWeekResponse,
)
from app.services import weekly_content_service as svc
from app.services.international_events_service import list_events, sync_calendar_events

router = APIRouter()


@router.get("/settings", response_model=WeeklyContentSettingsResponse)
async def get_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = svc.get_or_create_settings(db, current_user.id)
    return WeeklyContentSettingsResponse(**svc.settings_to_dict(row))


@router.patch("/settings", response_model=WeeklyContentSettingsResponse)
async def patch_settings(
    body: WeeklyContentSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        row = svc.update_settings(
            db,
            current_user.id,
            **body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return WeeklyContentSettingsResponse(**svc.settings_to_dict(row))


@router.get("/events", response_model=CalendarEventsResponse)
async def get_events(
    date_from: Optional[date] = Query(default=None, alias="from"),
    date_to: Optional[date] = Query(default=None, alias="to"),
    sync: bool = Query(default=True, description="Refresh UN/Nager seed before listing"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = svc.get_or_create_settings(db, current_user.id)
    start = date_from or date.today()
    end = date_to or date.fromordinal(start.toordinal() + 7)
    if end < start:
        start, end = end, start
    # Ensure seed data exists for the window
    years = sorted({start.year, end.year})
    if sync:
        await sync_calendar_events(db, countries=list(settings.countries or []), years=years)
    rows = list_events(
        db,
        date_from=start,
        date_to=end,
        countries=list(settings.countries or []),
        user_id=current_user.id,
        limit=1000,
    )
    items = [
        CalendarEventItem(
            id=r.id,
            event_date=r.event_date,
            title=r.title,
            country_or_scope=r.country_or_scope,
            source=r.source,
            description=r.description,
        )
        for r in rows
    ]
    return CalendarEventsResponse(items=items, total=len(items))


@router.post("/social-topics", response_model=SocialTopicResponse, status_code=status.HTTP_201_CREATED)
async def create_social_topic(
    body: SocialTopicCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        data = svc.create_social_topic(
            db,
            current_user.id,
            topic=body.topic,
            publish_date=body.publish_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return SocialTopicResponse(**data)


@router.delete("/social-topics/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_social_topic(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        deleted = svc.delete_social_topic(db, current_user.id, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
    return None


@router.get("/social-week", response_model=WeeklySocialWeekResponse)
async def get_social_week(
    week: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = svc.get_social_week(db, current_user.id, week=week)
    return WeeklySocialWeekResponse(
        iso_week=data["iso_week"],
        items=[WeeklySocialItemResponse(**item) for item in data["items"]],
        upcoming_events=[CalendarEventItem(**e) for e in data["upcoming_events"]],
    )


@router.delete("/social-week/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_social_week_item(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    deleted = svc.delete_weekly_social_item(db, current_user.id, item_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto post not found")
    return None


@router.post("/keywords/csv", response_model=KeywordCsvUploadResponse)
async def upload_keywords_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")
    if len(raw) > 1_000_000:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="CSV must be 1MB or smaller",
        )
    filename = file.filename or "keywords.csv"
    try:
        import_row, created = svc.import_keywords_csv(
            db,
            current_user.id,
            filename=filename,
            content=raw,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return KeywordCsvUploadResponse(
        import_id=import_row.id,
        filename=import_row.filename,
        row_count=import_row.row_count,
        items_created=created,
    )


@router.get("/keywords", response_model=KeywordPlanListResponse)
async def list_keywords(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = svc.list_keyword_items(db, current_user.id, status=status_filter)
    pending = svc.pending_keyword_count(db, current_user.id)
    items = [
        KeywordPlanItemResponse(
            id=r.id,
            keyword=r.keyword,
            category=r.category,
            priority=r.priority,
            status=r.status,
            iso_week=r.iso_week,
            used_at=r.used_at,
            workflow_id=r.workflow_id,
            wordpress_link=r.wordpress_link,
            social_post_history_id=r.social_post_history_id,
            error_message=r.error_message,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return KeywordPlanListResponse(items=items, total=len(items), pending_count=pending)


@router.delete("/keywords/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_keyword(
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    deleted = svc.delete_keyword_plan_item(db, current_user.id, item_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Keyword not found")
    return None


@router.get("/blog-week", response_model=WeeklyBlogWeekResponse)
async def get_blog_week(
    week: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = svc.get_blog_week(db, current_user.id, week=week)
    items = [
        WeeklyBlogItemResponse(
            id=r.id,
            keyword=r.keyword,
            category=r.category,
            status=r.status,
            iso_week=r.iso_week,
            workflow_id=r.workflow_id,
            wordpress_link=r.wordpress_link,
            social_post_history_id=r.social_post_history_id,
            result_json=r.result_json,
            error_message=r.error_message,
            used_at=r.used_at,
        )
        for r in data["items"]
    ]
    return WeeklyBlogWeekResponse(
        iso_week=data["iso_week"],
        items=items,
        pending_count=data["pending_count"],
    )


@router.post("/run", response_model=WeeklyRunResponse)
async def run_weekly_now(
    force: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = await svc.run_weekly_for_user(
        db,
        current_user,
        trigger="manual",
        force=force,
    )
    return WeeklyRunResponse(
        run_id=run.id,
        iso_week=run.iso_week,
        status=run.status,
        trigger=run.trigger,
        result_summary=run.result_summary,
        error_summary=run.error_summary,
    )


@router.get("/readiness", response_model=WeeklyReadinessResponse)
async def get_readiness(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return WeeklyReadinessResponse(**svc.get_weekly_readiness(db, current_user.id))


@router.post("/cron/run-due", response_model=WeeklyCronRunResponse)
async def cron_run_due(
    force: bool = Query(default=False),
    authorization: Optional[str] = Header(default=None),
):
    """Scheduler entry for auto-publish. Auth: Bearer WEEKLY_CRON_SECRET.

    Ticks hourly; each user runs only when their saved schedule is due.
    """
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
        ran = await svc.run_due_weekly_content(force=force)
    except Exception as exc:
        # Surface the root cause to the scheduler log / operator; generic 500s
        # hide DB/calendar failures and make free-tier debugging impossible.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"cron run-due failed: {type(exc).__name__}: {exc}",
        ) from exc
    return WeeklyCronRunResponse(ran=ran, iso_week=svc.iso_week_key(), force=force)
