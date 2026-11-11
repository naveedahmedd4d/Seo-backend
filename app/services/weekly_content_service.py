"""Fully-auto weekly pipelines: event social posts + CSV blog plan."""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.social_post_history import SocialPostHistory
from app.models.user import User
from app.models.weekly_content import (
    CalendarEvent,
    KeywordPlanImport,
    KeywordPlanItem,
    WeeklyContentRun,
    WeeklyContentSettings,
    WeeklySocialItem,
)
from app.models.wordpress_site import WordPressSite
from app.services.international_events_service import (
    DEFAULT_COUNTRIES,
    current_iso_week_window,
    list_events,
    sync_calendar_events,
    upcoming_week_window,
)
from app.services.social_account_service import get_user_social_accounts

logger = logging.getLogger(__name__)

OUTREACH_PLATFORMS = {"devto", "hashnode", "blogger"}
SOCIAL_IMAGE_PLATFORMS = {
    "facebook",
    "instagram",
    "x",
    "linkedin",
    "pinterest",
    "mastodon",
    "bluesky",
    "reddit",
}


def iso_week_key(d: Optional[date] = None) -> str:
    target = d or date.today()
    year, week, _ = target.isocalendar()
    return f"{year}-W{week:02d}"


def week_bucket_for_date(publish_date: date, *, today: Optional[date] = None) -> str:
    """Classify a publish date relative to today: current | next | upcoming | past."""
    today = today or date.today()
    current_start, current_end = current_iso_week_window(today)
    next_start = current_end + timedelta(days=1)
    next_end = next_start + timedelta(days=6)
    if publish_date < current_start:
        return "past"
    if current_start <= publish_date <= current_end:
        return "current"
    if next_start <= publish_date <= next_end:
        return "next"
    return "upcoming"


def _custom_topic_external_id(user_id: int) -> str:
    return f"user-{user_id}-topic-{uuid.uuid4().hex[:12]}"


def _is_user_custom_event(event: CalendarEvent, user_id: int) -> bool:
    return event.source == "custom" and (event.external_id or "").startswith(f"user-{user_id}-")


def create_social_topic(
    db: Session,
    user_id: int,
    *,
    topic: str,
    publish_date: date,
) -> dict[str, Any]:
    """Queue a custom social topic into the ISO week of publish_date."""
    title = " ".join((topic or "").split()).strip()
    if not title:
        raise ValueError("Topic is required")
    if len(title) > 255:
        raise ValueError("Topic must be 255 characters or fewer")

    week = iso_week_key(publish_date)
    bucket = week_bucket_for_date(publish_date)
    event = CalendarEvent(
        event_date=publish_date,
        title=title,
        country_or_scope="CUSTOM",
        source="custom",
        external_id=_custom_topic_external_id(user_id),
        description=f"Custom social topic scheduled for {publish_date.isoformat()}",
    )
    db.add(event)
    db.flush()

    item = WeeklySocialItem(
        user_id=user_id,
        iso_week=week,
        calendar_event_id=event.id,
        status="queued",
    )
    db.add(item)
    db.commit()
    db.refresh(event)
    db.refresh(item)

    return {
        "event_id": event.id,
        "item_id": item.id,
        "topic": event.title,
        "publish_date": event.event_date,
        "iso_week": week,
        "week_bucket": bucket,
        "status": item.status,
        "source": event.source,
    }


def delete_social_topic(db: Session, user_id: int, event_id: int) -> bool:
    """Delete a custom topic (and its weekly social item). UN/Nager events cannot be deleted."""
    event = db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
    if not event:
        return False
    if not _is_user_custom_event(event, user_id):
        raise ValueError("Only custom topics you created can be deleted")

    (
        db.query(WeeklySocialItem)
        .filter(
            WeeklySocialItem.user_id == user_id,
            WeeklySocialItem.calendar_event_id == event.id,
        )
        .delete(synchronize_session=False)
    )
    db.delete(event)
    db.commit()
    return True


def delete_weekly_social_item(db: Session, user_id: int, item_id: int) -> bool:
    """Remove one auto-post row for this week so it can be retried or cleared.

    Does not delete the calendar event (UN/custom topic stays). Does not
    unpublish anything already live on social platforms.
    """
    row = (
        db.query(WeeklySocialItem)
        .filter(WeeklySocialItem.id == item_id, WeeklySocialItem.user_id == user_id)
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


async def publish_calendar_event_now(
    db: Session,
    user: User,
    event_id: int,
) -> dict[str, Any]:
    """Generate + publish a single observance/topic to connected social accounts."""
    event = db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
    if not event:
        raise ValueError("Event not found")
    if event.source == "custom" and not _is_user_custom_event(event, user.id):
        raise ValueError("Event not found")

    platforms = _connected_platforms(db, user.id, include_outreach=False)
    week = iso_week_key()

    run = (
        db.query(WeeklyContentRun)
        .filter(WeeklyContentRun.user_id == user.id, WeeklyContentRun.iso_week == week)
        .first()
    )
    if not run:
        run = WeeklyContentRun(
            user_id=user.id,
            iso_week=week,
            status="running",
            trigger="manual_event",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

    item = (
        db.query(WeeklySocialItem)
        .filter(
            WeeklySocialItem.user_id == user.id,
            WeeklySocialItem.iso_week == week,
            WeeklySocialItem.calendar_event_id == event.id,
        )
        .first()
    )
    created = False
    if not item:
        item = WeeklySocialItem(
            user_id=user.id,
            run_id=run.id,
            iso_week=week,
            calendar_event_id=event.id,
            status="queued",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        created = True
    elif item.status == "published":
        # Already live — do not post again unless the row is cleared/deleted first.
        return {
            "event_id": event.id,
            "item_id": item.id,
            "iso_week": week,
            "status": item.status,
            "event_title": event.title,
            "error_message": None,
            "publish_results": item.publish_results,
            "created": False,
        }
    else:
        item.run_id = run.id
        item.status = "generating"
        item.error_message = None
        db.commit()

    if not platforms:
        item.status = "failed"
        item.error_message = "No connected social accounts"
        db.commit()
        return {
            "event_id": event.id,
            "item_id": item.id,
            "iso_week": week,
            "status": item.status,
            "event_title": event.title,
            "error_message": item.error_message,
            "publish_results": None,
            "created": created,
        }

    try:
        item.status = "generating"
        db.commit()
        snippet = event.description or (
            f"Custom social topic: {event.title}"
            if event.source == "custom"
            else f"International observance: {event.title}"
        )
        history, results = await _generate_and_publish_social(
            db,
            user,
            query=event.title,
            source_title=event.title,
            source_snippet=snippet,
            source_url=None,
            platforms=platforms,
            settings_snapshot={
                "source": "weekly_content_goal1_manual",
                "calendar_event_id": event.id,
                "iso_week": week,
                "platforms": platforms,
                "custom_topic": event.source == "custom",
            },
        )
        item.social_post_history_id = history.id
        item.publish_results = results
        item.status = "published" if any(r.get("success") for r in results) else "failed"
        if item.status == "failed":
            item.error_message = "All platform publishes failed"
        else:
            item.error_message = None
        db.commit()
    except Exception as exc:
        logger.exception("Manual publish failed for user %s event %s", user.id, event.id)
        item.status = "failed"
        item.error_message = str(exc)[:2000]
        db.commit()

    return {
        "event_id": event.id,
        "item_id": item.id,
        "iso_week": week,
        "status": item.status,
        "event_title": event.title,
        "error_message": item.error_message,
        "publish_results": item.publish_results,
        "created": created,
    }


def get_or_create_settings(db: Session, user_id: int) -> WeeklyContentSettings:
    row = db.query(WeeklyContentSettings).filter(WeeklyContentSettings.user_id == user_id).first()
    if row:
        if row.countries is None:
            row.countries = list(DEFAULT_COUNTRIES)
            db.commit()
            db.refresh(row)
        return row
    row = WeeklyContentSettings(
        user_id=user_id,
        countries=list(DEFAULT_COUNTRIES),
        max_social_per_week=7,
        blogs_per_week=2,
        run_frequency="weekly",
        goal1_enabled=False,
        goal2_enabled=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_settings(
    db: Session,
    user_id: int,
    **changes: Any,
) -> WeeklyContentSettings:
    row = get_or_create_settings(db, user_id)
    if "goal1_enabled" in changes and changes["goal1_enabled"] is not None:
        row.goal1_enabled = bool(changes["goal1_enabled"])
    if "goal2_enabled" in changes and changes["goal2_enabled"] is not None:
        row.goal2_enabled = bool(changes["goal2_enabled"])
    if "countries" in changes and changes["countries"] is not None:
        cleaned = [str(c).strip().upper() for c in changes["countries"] if str(c).strip()]
        row.countries = cleaned or list(DEFAULT_COUNTRIES)
    if "max_social_per_week" in changes and changes["max_social_per_week"] is not None:
        row.max_social_per_week = max(1, min(30, int(changes["max_social_per_week"])))
    if "blogs_per_week" in changes and changes["blogs_per_week"] is not None:
        row.blogs_per_week = max(1, min(10, int(changes["blogs_per_week"])))
    if "wordpress_site_id" in changes:
        site_id = changes["wordpress_site_id"]
        if site_id is None:
            row.wordpress_site_id = None
        else:
            site = (
                db.query(WordPressSite)
                .filter(WordPressSite.id == int(site_id), WordPressSite.user_id == user_id)
                .first()
            )
            if not site:
                raise ValueError("WordPress site not found")
            row.wordpress_site_id = site.id
    if "cron_weekday" in changes and changes["cron_weekday"] is not None:
        row.cron_weekday = max(0, min(6, int(changes["cron_weekday"])))
    if "run_frequency" in changes and changes["run_frequency"] is not None:
        value = str(changes["run_frequency"]).strip().lower()
        if value not in ("weekly", "daily"):
            raise ValueError("run_frequency must be 'weekly' or 'daily'")
        row.run_frequency = value
    if "cron_time_utc" in changes and changes["cron_time_utc"] is not None:
        value = str(changes["cron_time_utc"]).strip()
        if not re.fullmatch(r"\d{1,2}:\d{2}", value):
            raise ValueError("cron_time_utc must be HH:MM")
        hour, minute = value.split(":")
        row.cron_time_utc = f"{int(hour):02d}:{int(minute):02d}"
    db.commit()
    db.refresh(row)
    return row


def settings_to_dict(row: WeeklyContentSettings) -> dict:
    return {
        "goal1_enabled": bool(row.goal1_enabled),
        "goal2_enabled": bool(row.goal2_enabled),
        "countries": list(row.countries or DEFAULT_COUNTRIES),
        "max_social_per_week": int(row.max_social_per_week or 7),
        "blogs_per_week": int(row.blogs_per_week or 2),
        "wordpress_site_id": row.wordpress_site_id,
        "cron_weekday": int(row.cron_weekday if row.cron_weekday is not None else 0),
        "cron_time_utc": row.cron_time_utc or "09:00",
        "run_frequency": (row.run_frequency or "weekly"),
        "last_run_at": row.last_run_at,
        "last_run_iso_week": row.last_run_iso_week,
        "updated_at": row.updated_at,
    }


def _connected_platforms(db: Session, user_id: int, *, include_outreach: bool) -> list[str]:
    accounts = get_user_social_accounts(db, user_id)
    platforms: list[str] = []
    for account in accounts:
        platform = (account.platform or "").strip().lower()
        if not platform:
            continue
        if platform in SOCIAL_IMAGE_PLATFORMS:
            platforms.append(platform)
        elif include_outreach and platform in OUTREACH_PLATFORMS:
            platforms.append(platform)
    # Preserve order, unique
    seen: set[str] = set()
    ordered: list[str] = []
    for platform in platforms:
        if platform not in seen:
            seen.add(platform)
            ordered.append(platform)
    return ordered


async def _generate_and_publish_social(
    db: Session,
    user: User,
    *,
    query: str,
    source_title: str,
    source_snippet: str,
    source_url: Optional[str],
    platforms: list[str],
    settings_snapshot: dict,
    image_url: Optional[str] = None,
    body_html: Optional[str] = None,
) -> tuple[SocialPostHistory, list[dict]]:
    from dataclasses import replace

    from app.services.llm_settings_service import resolve_llm_credentials
    from app.services.openrouter_service import (
        generate_news_headline,
        generate_social_post_description,
        generate_social_post_hashtags,
        generate_social_post_story_description,
        reset_llm_context,
        set_llm_context,
    )
    from app.services.social_post_copy_service import PublishCopyContext
    from app.services.social_post_link_service import resolve_facebook_destination_url
    from app.services.social_post_platform_publish import publish_to_platform
    from app.services.social_post_publish_service import finalize_history_status, record_publications

    source = [{
        "title": source_title,
        "source": "Weekly automation",
        "snippet": (source_snippet or source_title)[:1200],
        "link": source_url,
        "date": date.today().isoformat(),
    }]
    token = set_llm_context(resolve_llm_credentials(db, user.id))
    try:
        headline = await generate_news_headline(source, query=query)
        description = await generate_social_post_description(source, headline, query)
        story = await generate_social_post_story_description(source, headline, query)
        hashtags = await generate_social_post_hashtags(source, headline, query, 12)
    finally:
        reset_llm_context(token)

    # For outreach article platforms, prefer HTML body as story/caption payload.
    story_for_history = story
    if body_html and any(p in OUTREACH_PLATFORMS for p in platforms):
        story_for_history = body_html[:20000]

    history = SocialPostHistory(
        user_id=user.id,
        query=query,
        headline=headline,
        description=description,
        story_description=story_for_history,
        hashtags=hashtags,
        settings_snapshot=settings_snapshot,
        news_results=source,
        publish_status="draft",
    )
    db.add(history)
    db.commit()
    db.refresh(history)

    if not platforms:
        return history, []

    needs_image = any(p in SOCIAL_IMAGE_PLATFORMS for p in platforms)
    resolved_image_url = image_url
    if needs_image and not (resolved_image_url and str(resolved_image_url).startswith(("http://", "https://"))):
        from app.services.weekly_social_media import ensure_weekly_social_media

        # Keep LLM context for the photo picker (same credentials as caption gen).
        pick_token = set_llm_context(resolve_llm_credentials(db, user.id))
        try:
            resolved_image_url = await ensure_weekly_social_media(
                db,
                user,
                history,
                query=query,
                headline=headline,
                description=description or "",
                story=story,
                hashtags=hashtags if isinstance(hashtags, list) else None,
                existing_image_url=image_url,
            )
        finally:
            reset_llm_context(pick_token)

    from app.schemas.social_post import SocialPostPublishResultItem

    ctx = PublishCopyContext(
        headline=headline,
        short_description=description,
        story_caption=story_for_history if body_html else story,
        hashtags=hashtags or [],
        source_url=source_url,
    )
    results = []
    for platform in platforms:
        platform_ctx = ctx
        if platform == "facebook":
            first_party_url = await resolve_facebook_destination_url(db, user.id, history)
            platform_ctx = replace(ctx, source_url=first_party_url)
            if not resolved_image_url:
                results.append(
                    SocialPostPublishResultItem(
                        platform="facebook",
                        success=False,
                        error=(
                            "No image available for Facebook Page posts "
                            "(card render and image search both failed)"
                        ),
                    )
                )
                continue
        if platform == "instagram" and not resolved_image_url:
            results.append(
                SocialPostPublishResultItem(
                    platform="instagram",
                    success=False,
                    error="No image available for Instagram posts",
                )
            )
            continue
        results.append(
            await publish_to_platform(
                db,
                user.id,
                platform,
                ctx=platform_ctx,
                image_url=resolved_image_url,
                platform_options={},
            )
        )
    record_publications(db, user.id, history.id, results)
    finalize_history_status(db, history, results)
    return history, [r.model_dump(mode="json") for r in results]


def _goal1_events_for_week(
    db: Session,
    user_id: int,
    *,
    week: str,
    countries: list[str],
    cap: int,
) -> list[CalendarEvent]:
    """UN/Nager events in the rolling week window + this user's custom topics for `week`."""
    date_from, date_to = upcoming_week_window()
    events = list_events(
        db,
        date_from=date_from,
        date_to=date_to,
        countries=countries,
        user_id=user_id,
    )
    # Prefer already-queued custom topics for this ISO week even if outside today..+6.
    queued_custom_ids = [
        row.calendar_event_id
        for row in (
            db.query(WeeklySocialItem)
            .filter(
                WeeklySocialItem.user_id == user_id,
                WeeklySocialItem.iso_week == week,
                WeeklySocialItem.status.in_(("queued", "failed", "generating")),
            )
            .all()
        )
    ]
    if queued_custom_ids:
        extras = (
            db.query(CalendarEvent)
            .filter(
                CalendarEvent.id.in_(queued_custom_ids),
                CalendarEvent.source == "custom",
            )
            .all()
        )
        seen = {e.id for e in events}
        for event in extras:
            if event.id not in seen and _is_user_custom_event(event, user_id):
                events.append(event)
                seen.add(event.id)

    filtered: list[CalendarEvent] = []
    for event in events:
        if event.source == "custom":
            if not _is_user_custom_event(event, user_id):
                continue
            # Only publish custom topics in the ISO week they were scheduled for.
            if iso_week_key(event.event_date) != week:
                continue
        filtered.append(event)
    return filtered[:cap]


async def run_goal1_for_user(
    db: Session,
    user: User,
    settings: WeeklyContentSettings,
    run: WeeklyContentRun,
) -> dict:
    week = run.iso_week
    countries = list(settings.countries or DEFAULT_COUNTRIES)
    cap = max(1, int(settings.max_social_per_week or 7))
    events = _goal1_events_for_week(db, user.id, week=week, countries=countries, cap=cap)

    platforms = _connected_platforms(db, user.id, include_outreach=False)
    created = 0
    published = 0
    failed = 0

    if not events:
        logger.info("Goal1 skipped for user %s week %s: no upcoming events", user.id, week)
        return {
            "events": 0,
            "created": 0,
            "published": 0,
            "failed": 0,
            "skipped": "no_upcoming_events",
        }

    if not platforms:
        for event in events:
            item = (
                db.query(WeeklySocialItem)
                .filter(
                    WeeklySocialItem.user_id == user.id,
                    WeeklySocialItem.iso_week == week,
                    WeeklySocialItem.calendar_event_id == event.id,
                )
                .first()
            )
            if not item:
                item = WeeklySocialItem(
                    user_id=user.id,
                    run_id=run.id,
                    iso_week=week,
                    calendar_event_id=event.id,
                    status="failed",
                    error_message="No connected social accounts",
                )
                db.add(item)
            else:
                item.status = "failed"
                item.error_message = "No connected social accounts"
                item.run_id = run.id
            failed += 1
        db.commit()
        return {"events": len(events), "created": created, "published": published, "failed": failed}

    for event in events:
        item = (
            db.query(WeeklySocialItem)
            .filter(
                WeeklySocialItem.user_id == user.id,
                WeeklySocialItem.iso_week == week,
                WeeklySocialItem.calendar_event_id == event.id,
            )
            .first()
        )
        if item and item.status == "published":
            published += 1
            continue
        if not item:
            item = WeeklySocialItem(
                user_id=user.id,
                run_id=run.id,
                iso_week=week,
                calendar_event_id=event.id,
                status="queued",
            )
            db.add(item)
            db.commit()
            db.refresh(item)
            created += 1
        else:
            item.run_id = run.id
            item.status = "generating"
            item.error_message = None
            db.commit()

        try:
            item.status = "generating"
            db.commit()
            snippet = event.description or (
                f"Custom social topic: {event.title}"
                if event.source == "custom"
                else f"International observance: {event.title}"
            )
            history, results = await _generate_and_publish_social(
                db,
                user,
                query=event.title,
                source_title=event.title,
                source_snippet=snippet,
                source_url=None,
                platforms=platforms,
                settings_snapshot={
                    "source": "weekly_content_goal1",
                    "calendar_event_id": event.id,
                    "iso_week": week,
                    "platforms": platforms,
                    "custom_topic": event.source == "custom",
                },
            )
            item.social_post_history_id = history.id
            item.publish_results = results
            item.status = "published" if any(r.get("success") for r in results) else "failed"
            if item.status == "failed":
                item.error_message = "All platform publishes failed"
                failed += 1
            else:
                published += 1
            db.commit()
        except Exception as exc:
            logger.exception("Goal1 social failed for user %s event %s", user.id, event.id)
            item.status = "failed"
            item.error_message = str(exc)[:2000]
            failed += 1
            db.commit()

    return {"events": len(events), "created": created, "published": published, "failed": failed}


def import_keywords_csv(
    db: Session,
    user_id: int,
    *,
    filename: str,
    content: bytes,
) -> tuple[KeywordPlanImport, int]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    fields = {f.strip().lower(): f for f in reader.fieldnames if f}
    keyword_key = fields.get("keyword") or fields.get("keywords") or fields.get("topic")
    if not keyword_key:
        # Allow headerless single-column: re-parse as plain lines
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            raise ValueError("CSV is empty")
        # If first line looks like a header named keyword, skip it
        start = 1 if lines[0].lower() in {"keyword", "keywords", "topic"} else 0
        import_row = KeywordPlanImport(user_id=user_id, filename=filename, row_count=0)
        db.add(import_row)
        db.commit()
        db.refresh(import_row)
        created = 0
        for line in lines[start:]:
            keyword = line.split(",")[0].strip().strip('"')
            if not keyword:
                continue
            db.add(
                KeywordPlanItem(
                    user_id=user_id,
                    import_id=import_row.id,
                    keyword=keyword[:255],
                    category=None,
                    priority=100,
                    status="pending",
                )
            )
            created += 1
        import_row.row_count = created
        db.commit()
        return import_row, created

    category_key = fields.get("category")
    priority_key = fields.get("priority")
    import_row = KeywordPlanImport(user_id=user_id, filename=filename, row_count=0)
    db.add(import_row)
    db.commit()
    db.refresh(import_row)
    created = 0
    for row in reader:
        keyword = str(row.get(keyword_key) or "").strip()
        if not keyword:
            continue
        category = str(row.get(category_key) or "").strip() if category_key else None
        priority = 100
        if priority_key:
            try:
                priority = int(str(row.get(priority_key) or "100").strip())
            except ValueError:
                priority = 100
        db.add(
            KeywordPlanItem(
                user_id=user_id,
                import_id=import_row.id,
                keyword=keyword[:255],
                category=(category[:120] if category else None),
                priority=priority,
                status="pending",
            )
        )
        created += 1
    import_row.row_count = created
    db.commit()
    db.refresh(import_row)
    return import_row, created


def list_keyword_items(
    db: Session,
    user_id: int,
    *,
    status: Optional[str] = None,
    limit: int = 200,
) -> list[KeywordPlanItem]:
    query = db.query(KeywordPlanItem).filter(KeywordPlanItem.user_id == user_id)
    if status:
        query = query.filter(KeywordPlanItem.status == status)
    return (
        query.order_by(KeywordPlanItem.priority.asc(), KeywordPlanItem.id.asc())
        .limit(limit)
        .all()
    )


def pending_keyword_count(db: Session, user_id: int) -> int:
    return (
        db.query(KeywordPlanItem)
        .filter(KeywordPlanItem.user_id == user_id, KeywordPlanItem.status == "pending")
        .count()
    )


def delete_keyword_plan_item(db: Session, user_id: int, item_id: int) -> bool:
    """Remove a queue row owned by the user. Does not unpublish WordPress/social posts."""
    row = (
        db.query(KeywordPlanItem)
        .filter(KeywordPlanItem.id == item_id, KeywordPlanItem.user_id == user_id)
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


async def run_goal2_for_user(
    db: Session,
    user: User,
    settings: WeeklyContentSettings,
    run: WeeklyContentRun,
) -> dict:
    from app.api.blog import publish_blog, run_blog_workflow
    from app.models.workflow_state import WorkflowState, WorkflowStatus, WorkflowType
    from app.schemas.blog import BlogPublishRequest

    week = run.iso_week
    limit = max(1, int(settings.blogs_per_week or 2))
    # Only successful publishes consume weekly quota so transient failures can retry.
    already_used = (
        db.query(KeywordPlanItem)
        .filter(
            KeywordPlanItem.user_id == user.id,
            KeywordPlanItem.iso_week == week,
            KeywordPlanItem.status == "used",
        )
        .count()
    )
    remaining = max(0, limit - already_used)
    if remaining == 0:
        return {"selected": 0, "published": already_used, "failed": 0, "skipped": "week_quota_met"}

    if not settings.wordpress_site_id:
        return {"selected": 0, "published": 0, "failed": 0, "error": "wordpress_site_id not configured"}

    site = (
        db.query(WordPressSite)
        .filter(WordPressSite.id == settings.wordpress_site_id, WordPressSite.user_id == user.id)
        .first()
    )
    if not site:
        return {"selected": 0, "published": 0, "failed": 0, "error": "WordPress site not found"}

    # Prefer true pending rows; also retry earlier failed items that never published to WP.
    items = (
        db.query(KeywordPlanItem)
        .filter(
            KeywordPlanItem.user_id == user.id,
            KeywordPlanItem.status == "pending",
        )
        .order_by(KeywordPlanItem.priority.asc(), KeywordPlanItem.id.asc())
        .limit(remaining)
        .all()
    )
    if len(items) < remaining:
        retry_needed = remaining - len(items)
        retries = (
            db.query(KeywordPlanItem)
            .filter(
                KeywordPlanItem.user_id == user.id,
                KeywordPlanItem.status == "failed",
                KeywordPlanItem.wordpress_post_id.is_(None),
            )
            .order_by(KeywordPlanItem.priority.asc(), KeywordPlanItem.id.asc())
            .limit(retry_needed)
            .all()
        )
        for row in retries:
            row.status = "pending"
            row.error_message = None
            row.iso_week = None
        if retries:
            db.commit()
            items = items + retries

    if not items:
        logger.info(
            "Goal2 skipped for user %s week %s: empty keyword queue (pending=%s)",
            user.id,
            week,
            pending_keyword_count(db, user.id),
        )
        return {
            "selected": 0,
            "published": already_used,
            "failed": 0,
            "skipped": "empty_keyword_queue",
        }

    platforms = _connected_platforms(db, user.id, include_outreach=True)
    published = 0
    failed = 0

    for item in items:
        # Skip if the user deleted this queue row while the run was in progress.
        still = (
            db.query(KeywordPlanItem.id)
            .filter(KeywordPlanItem.id == item.id, KeywordPlanItem.user_id == user.id)
            .first()
        )
        if not still:
            continue
        item.error_message = None
        db.commit()
        publish_data: dict[str, Any] | None = None
        workflow_id: int | None = None
        try:
            # If a previous attempt already published to WP, do not republish — only share.
            if item.wordpress_post_id and item.wordpress_link:
                publish_data = {
                    "post_id": item.wordpress_post_id,
                    "link": item.wordpress_link,
                    "reused": True,
                }
                title = item.keyword
                body = ""
                meta = ""
                image_url = None
                link = item.wordpress_link
                workflow_id = item.workflow_id
            else:
                workflow = WorkflowState(
                    user_id=user.id,
                    workflow_type=WorkflowType.BLOG,
                    status=WorkflowStatus.PENDING,
                    state_data={
                        "topic": item.keyword,
                        "description": None,
                        "keywords": [item.keyword],
                        "category": item.category or "Technology & AI",
                        "source": "weekly_content_goal2",
                        "iso_week": week,
                    },
                )
                db.add(workflow)
                db.commit()
                db.refresh(workflow)
                workflow_id = workflow.id

                await run_blog_workflow(
                    db,
                    workflow.id,
                    item.keyword,
                    None,
                    [item.keyword],
                    item.category or "Technology & AI",
                    user.id,
                    include_research=True,
                )
                db.refresh(workflow)
                if workflow.status != WorkflowStatus.COMPLETED:
                    raise ValueError(workflow.error_message or "Blog generation failed")

                publish_response = await publish_blog(
                    BlogPublishRequest(
                        workflow_id=workflow.id,
                        wordpress_site_id=site.id,
                        status="publish",
                        source_category=item.category,
                    ),
                    current_user=user,
                    db=db,
                )
                publish_data = publish_response.model_dump(mode="json")
                # Persist WP result immediately so a later social failure does not orphan the post.
                live = (
                    db.query(KeywordPlanItem)
                    .filter(KeywordPlanItem.id == item.id, KeywordPlanItem.user_id == user.id)
                    .first()
                )
                if live:
                    live.workflow_id = workflow.id
                    live.wordpress_post_id = publish_data.get("post_id")
                    live.wordpress_link = publish_data.get("link")
                    db.commit()

                state = workflow.state_data or {}
                title = state.get("title") or item.keyword
                body = state.get("body_content") or ""
                meta = state.get("meta_description") or ""
                featured = state.get("featured_image")
                image_url = featured.get("url") if isinstance(featured, dict) else featured
                link = publish_data.get("link")

            social_history_id = item.social_post_history_id
            social_results: list[dict] = []
            social_error: str | None = None
            if platforms and not social_history_id:
                try:
                    history, social_results = await _generate_and_publish_social(
                        db,
                        user,
                        query=item.keyword,
                        source_title=title,
                        source_snippet=meta or (body[:800] if isinstance(body, str) else ""),
                        source_url=link,
                        platforms=platforms,
                        settings_snapshot={
                            "source": "weekly_content_goal2",
                            "keyword_plan_item_id": item.id,
                            "iso_week": week,
                            "platforms": platforms,
                        },
                        image_url=image_url if isinstance(image_url, str) else None,
                        body_html=body if isinstance(body, str) else None,
                    )
                    social_history_id = history.id
                except Exception as social_exc:
                    logger.exception(
                        "Goal2 social share failed for user %s keyword %s", user.id, item.id
                    )
                    social_error = str(social_exc)[:2000]

            # WP success consumes quota even if social sharing failed.
            live = (
                db.query(KeywordPlanItem)
                .filter(KeywordPlanItem.id == item.id, KeywordPlanItem.user_id == user.id)
                .first()
            )
            if not live:
                published += 1
                continue
            live.status = "used"
            live.iso_week = week
            live.used_at = datetime.now(timezone.utc)
            live.workflow_id = workflow_id
            if publish_data:
                live.wordpress_post_id = publish_data.get("post_id") or live.wordpress_post_id
                live.wordpress_link = publish_data.get("link") or live.wordpress_link
            live.social_post_history_id = social_history_id
            live.result_json = {
                "publish": publish_data,
                "social": social_results,
                "social_error": social_error,
            }
            live.error_message = social_error
            published += 1
            db.commit()
        except Exception as exc:
            logger.exception("Goal2 blog failed for user %s keyword %s", user.id, item.id)
            live = (
                db.query(KeywordPlanItem)
                .filter(KeywordPlanItem.id == item.id, KeywordPlanItem.user_id == user.id)
                .first()
            )
            if not live:
                failed += 1
                continue
            live.status = "failed"
            # Keep iso_week unset unless WP already published (handled above as used).
            live.error_message = str(exc)[:2000]
            failed += 1
            db.commit()

    return {"selected": len(items), "published": published, "failed": failed}


DEFAULT_CRON_TIME_UTC = "09:00"
# How far past the chosen time a tick may still fire. The scheduler wakes hourly
# and a cold free-tier instance can take ~50s, so a tick that lands late in the
# hour must still count as "due" rather than skipping the whole week.
RUN_DUE_GRACE_MINUTES = 90


def _parse_hhmm(value: Optional[str]) -> tuple[int, int]:
    """Parse a stored HH:MM, falling back to 09:00 on anything malformed."""
    raw = (value or DEFAULT_CRON_TIME_UTC).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if not match:
        return 9, 0
    hour = max(0, min(23, int(match.group(1))))
    minute = max(0, min(59, int(match.group(2))))
    return hour, minute


def is_run_due(
    settings: WeeklyContentSettings,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Has this user's configured schedule arrived in the current period?

    Weekly honours cron_weekday + cron_time_utc; daily honours the time only and
    fires once per calendar day. Both compare in UTC, which is how the settings
    are stored — the UI converts to the user's zone for display.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    frequency = (settings.run_frequency or "weekly").strip().lower()
    hour, minute = _parse_hhmm(settings.cron_time_utc)

    scheduled = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if frequency == "daily":
        # Already fired today, so wait for tomorrow's slot.
        if settings.last_run_date == moment.date():
            return False
    else:
        weekday = int(settings.cron_weekday if settings.cron_weekday is not None else 0)
        weekday = max(0, min(6, weekday))
        if moment.weekday() != weekday:
            return False
        if settings.last_run_iso_week == iso_week_key(moment.date()):
            return False

    if moment < scheduled:
        return False
    # Guard against a tick that wakes hours late firing an off-schedule run.
    return (moment - scheduled) <= timedelta(minutes=RUN_DUE_GRACE_MINUTES)


def _is_stale_running(run: WeeklyContentRun, *, max_age_hours: int = 2) -> bool:
    if run.status != "running" or not run.started_at:
        return False
    started = run.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - started
    return age.total_seconds() >= max_age_hours * 3600


async def run_weekly_for_user(
    db: Session,
    user: User,
    *,
    trigger: str = "scheduled",
    force: bool = False,
) -> WeeklyContentRun:
    settings = get_or_create_settings(db, user.id)
    week = iso_week_key()
    existing = (
        db.query(WeeklyContentRun)
        .filter(WeeklyContentRun.user_id == user.id, WeeklyContentRun.iso_week == week)
        .first()
    )
    if existing and existing.status == "completed" and not force:
        return existing
    if existing and existing.status == "running" and not force:
        if _is_stale_running(existing):
            existing.status = "failed"
            existing.error_summary = "Stale running state recovered after timeout"
            existing.finished_at = datetime.now(timezone.utc)
            db.commit()
        else:
            return existing

    run = existing or WeeklyContentRun(
        user_id=user.id,
        iso_week=week,
        status="queued",
        trigger=trigger,
    )
    if not existing:
        db.add(run)
        db.commit()
        db.refresh(run)

    run.status = "running"
    run.trigger = trigger
    run.started_at = datetime.now(timezone.utc)
    run.error_summary = None
    db.commit()

    summary: dict[str, Any] = {"goal1": None, "goal2": None, "events_sync": None}
    try:
        countries = list(settings.countries or DEFAULT_COUNTRIES)
        summary["events_sync"] = await sync_calendar_events(db, countries=countries)
        if settings.goal1_enabled:
            summary["goal1"] = await run_goal1_for_user(db, user, settings, run)
        if settings.goal2_enabled:
            summary["goal2"] = await run_goal2_for_user(db, user, settings, run)
        run.status = "completed"
        run.result_summary = summary
        run.finished_at = datetime.now(timezone.utc)
        settings.last_run_at = run.finished_at
        settings.last_run_iso_week = week
        settings.last_run_date = run.finished_at.date()
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        logger.exception("Weekly content run failed for user %s", user.id)
        run.status = "failed"
        run.error_summary = str(exc)[:2000]
        run.result_summary = summary
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
        return run


async def run_due_weekly_content(*, force: bool = False) -> int:
    """Cron entry: sync events globally then run enabled users for the current ISO week."""
    from app.database import SessionLocal

    db = SessionLocal()
    count = 0
    week = iso_week_key()
    try:
        logger.info("Weekly content cron started (week=%s force=%s)", week, force)
        rows = (
            db.query(WeeklyContentSettings)
            .filter(
                (WeeklyContentSettings.goal1_enabled.is_(True))
                | (WeeklyContentSettings.goal2_enabled.is_(True))
            )
            .all()
        )
        logger.info("Weekly content cron found %s enabled user setting(s)", len(rows))
        now = datetime.now(timezone.utc)
        due = [s for s in rows if force or is_run_due(s, now=now)]
        logger.info("Weekly content cron: %s of %s user(s) due at %s UTC", len(due), len(rows), now.isoformat())
        if not due:
            return 0
        # Only pay for the calendar sync once a run is actually happening.
        # Keep publishing even if the public calendar feed has a bad row —
        # existing events in DB are enough for the week.
        try:
            await sync_calendar_events(db, countries=DEFAULT_COUNTRIES)
        except Exception:
            logger.exception(
                "Calendar sync failed during weekly cron; continuing with existing events"
            )
            try:
                db.rollback()
            except Exception:
                logger.exception("Rollback after calendar sync failure also failed")
        for settings in due:
            user = db.query(User).filter(User.id == settings.user_id).first()
            if not user:
                continue
            if settings.last_run_iso_week == week and not force:
                existing = (
                    db.query(WeeklyContentRun)
                    .filter(
                        WeeklyContentRun.user_id == user.id,
                        WeeklyContentRun.iso_week == week,
                        WeeklyContentRun.status == "completed",
                    )
                    .first()
                )
                if existing:
                    logger.info("Skipping user %s — already completed %s", user.id, week)
                    continue
            await run_weekly_for_user(db, user, trigger="scheduled", force=force)
            count += 1
        logger.info("Weekly content cron finished — ran %s user(s)", count)
        return count
    finally:
        db.close()


def get_weekly_readiness(db: Session, user_id: int) -> dict[str, Any]:
    """Checklist of what must be true for scheduled auto-publish to succeed."""
    from app.config import settings as app_settings

    row = get_or_create_settings(db, user_id)
    social_platforms = _connected_platforms(db, user_id, include_outreach=False)
    outreach_platforms = _connected_platforms(db, user_id, include_outreach=True)
    pending = pending_keyword_count(db, user_id)
    date_from, date_to = upcoming_week_window()
    events = list_events(
        db,
        date_from=date_from,
        date_to=date_to,
        countries=list(row.countries or DEFAULT_COUNTRIES),
        user_id=user_id,
    )
    wp_ok = False
    wp_name: Optional[str] = None
    if row.wordpress_site_id:
        site = (
            db.query(WordPressSite)
            .filter(WordPressSite.id == row.wordpress_site_id, WordPressSite.user_id == user_id)
            .first()
        )
        if site:
            wp_ok = True
            wp_name = site.name or site.url

    max_social = int(row.max_social_per_week or 7)
    blogs_week = int(row.blogs_per_week or 2)
    cron_configured = bool(app_settings.weekly_cron_secret)

    checks: list[dict[str, Any]] = [
        {
            "id": "cron_secret",
            "label": "Scheduler secret configured",
            "ok": cron_configured,
            "detail": (
                "WEEKLY_CRON_SECRET is set on the API"
                if cron_configured
                else "Add WEEKLY_CRON_SECRET in the Render dashboard (Environment), then add the same value plus WEEKLY_CRON_URL as GitHub Actions repo secrets"
            ),
        },
        {
            "id": "goal1_enabled",
            "label": "Goal 1 · Weekly Social auto-publish",
            "ok": bool(row.goal1_enabled),
            "detail": "On" if row.goal1_enabled else "Turn on Auto publish on Weekly Social",
        },
        {
            "id": "social_accounts",
            "label": "Social accounts connected",
            "ok": len(social_platforms) > 0,
            "detail": ", ".join(social_platforms) if social_platforms else "Connect at least one social account",
        },
        {
            "id": "upcoming_events",
            "label": "Upcoming observances this week",
            "ok": len(events) > 0,
            "detail": f"{len(events)} event(s) · cap {max_social}/week",
        },
        {
            "id": "goal2_enabled",
            "label": "Goal 2 · Blogs List auto-publish",
            "ok": bool(row.goal2_enabled),
            "detail": "On" if row.goal2_enabled else "Turn on Auto publish on Blogs List",
        },
        {
            "id": "wordpress_site",
            "label": "WordPress site selected",
            "ok": wp_ok,
            "detail": wp_name or "Pick a WordPress site in Settings → Weekly Content",
        },
        {
            "id": "keyword_queue",
            "label": "Keyword queue stocked",
            "ok": pending >= blogs_week,
            "detail": f"{pending} pending · need ≥ {blogs_week}/week",
        },
        {
            "id": "quotas",
            "label": "Weekly quotas",
            "ok": max_social >= 1 and blogs_week >= 1,
            "detail": f"{max_social} social · {blogs_week} blogs per week",
        },
    ]

    goal1_ready = bool(row.goal1_enabled) and len(social_platforms) > 0 and len(events) > 0
    goal2_ready = bool(row.goal2_enabled) and wp_ok and pending >= blogs_week
    anything_enabled = bool(row.goal1_enabled or row.goal2_enabled)
    ready = cron_configured and anything_enabled and (
        (not row.goal1_enabled or goal1_ready) and (not row.goal2_enabled or goal2_ready)
    )

    last_run = (
        db.query(WeeklyContentRun)
        .filter(WeeklyContentRun.user_id == user_id)
        .order_by(WeeklyContentRun.id.desc())
        .first()
    )

    return {
        "ready": ready,
        "iso_week": iso_week_key(),
        "goal1_ready": goal1_ready,
        "goal2_ready": goal2_ready,
        "goal1_enabled": bool(row.goal1_enabled),
        "goal2_enabled": bool(row.goal2_enabled),
        "max_social_per_week": max_social,
        "blogs_per_week": blogs_week,
        "social_platforms": social_platforms,
        "outreach_platforms": [p for p in outreach_platforms if p in OUTREACH_PLATFORMS],
        "upcoming_events_count": len(events),
        "pending_keywords": pending,
        "wordpress_configured": wp_ok,
        "wordpress_site_name": wp_name,
        "cron_secret_configured": cron_configured,
        "last_run_at": row.last_run_at,
        "last_run_iso_week": row.last_run_iso_week,
        "last_run_status": last_run.status if last_run else None,
        "checks": checks,
    }


def get_social_week(db: Session, user_id: int, *, week: Optional[str] = None) -> dict:
    settings = get_or_create_settings(db, user_id)
    iso = week or iso_week_key()
    date_from, date_to = upcoming_week_window()
    events = list_events(
        db,
        date_from=date_from,
        date_to=date_to,
        countries=list(settings.countries or DEFAULT_COUNTRIES),
        user_id=user_id,
    )
    items = (
        db.query(WeeklySocialItem)
        .filter(WeeklySocialItem.user_id == user_id, WeeklySocialItem.iso_week == iso)
        .order_by(WeeklySocialItem.id.asc())
        .all()
    )
    history_ids = [i.social_post_history_id for i in items if i.social_post_history_id]
    histories = {}
    if history_ids:
        for row in db.query(SocialPostHistory).filter(SocialPostHistory.id.in_(history_ids)).all():
            histories[row.id] = row

    event_map = {e.id: e for e in db.query(CalendarEvent).filter(
        CalendarEvent.id.in_([i.calendar_event_id for i in items] or [-1])
    ).all()}

    payload_items = []
    for item in items:
        event = event_map.get(item.calendar_event_id)
        history = histories.get(item.social_post_history_id) if item.social_post_history_id else None
        payload_items.append({
            "id": item.id,
            "iso_week": item.iso_week,
            "status": item.status,
            "event_id": item.calendar_event_id,
            "event_date": event.event_date if event else date.today(),
            "event_title": event.title if event else "Unknown event",
            "event_scope": event.country_or_scope if event else "INT",
            "source": event.source if event else None,
            "social_post_history_id": item.social_post_history_id,
            "headline": history.headline if history else None,
            "publish_results": item.publish_results,
            "error_message": item.error_message,
            "created_at": item.created_at,
        })

    return {
        "iso_week": iso,
        "items": payload_items,
        "upcoming_events": [
            {
                "id": e.id,
                "event_date": e.event_date,
                "title": e.title,
                "country_or_scope": e.country_or_scope,
                "source": e.source,
                "description": e.description,
            }
            for e in events
        ],
    }


def get_blog_week(db: Session, user_id: int, *, week: Optional[str] = None) -> dict:
    iso = week or iso_week_key()
    items = (
        db.query(KeywordPlanItem)
        .filter(KeywordPlanItem.user_id == user_id, KeywordPlanItem.iso_week == iso)
        .order_by(KeywordPlanItem.id.asc())
        .all()
    )
    return {
        "iso_week": iso,
        "items": items,
        "pending_count": pending_keyword_count(db, user_id),
    }
