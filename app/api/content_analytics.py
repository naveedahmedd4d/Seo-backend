import logging
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.content_analytics import (
    AnalyticsSyncRun,
    BlogPublicationAttempt,
    GaOAuthToken,
    GaProperty,
    SocialPublicationAttempt,
)
from app.models.gsc_property import GscProperty
from app.models.published_post import PublishedPost
from app.models.social_account import SocialAccount
from app.models.wordpress_site import WordPressSite
from app.models.user import User
from app.services.content_analytics_service import (
    build_blog_rows,
    build_filters,
    build_keyword_performance,
    build_overview,
    build_social_rows,
    resolve_range,
)
from app.services.content_analytics_sync_service import execute_sync_run
from app.services.published_options_service import merge_options
from app.services.wordpress_service import list_published_content

router = APIRouter()

logger = logging.getLogger(__name__)


def _range(
    range_key: str,
    start_date: date | None,
    end_date: date | None,
    timezone_name: str,
):
    if range_key == "custom" and (not start_date or not end_date):
        raise HTTPException(status_code=422, detail="Custom ranges require start_date and end_date")
    return resolve_range(range_key, start_date, end_date, timezone_name)


@router.get("/overview")
async def overview(
    range: str = Query("30d", pattern="^(today|7d|30d|90d|custom)$"),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    timezone_name: str = Query("UTC", alias="timezone"),
    website_id: Optional[int] = None,
    platform: Optional[str] = None,
    category: Optional[str] = None,
    author: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return build_overview(
        db, current_user.id, _range(range, start_date, end_date, timezone_name),
        website_id, platform, category, author,
    )


@router.get("/blogs")
async def blogs(
    range: str = Query("30d", pattern="^(today|7d|30d|90d|custom)$"),
    start_date: Optional[date] = None, end_date: Optional[date] = None,
    timezone_name: str = Query("UTC", alias="timezone"),
    website_id: Optional[int] = None, category: Optional[str] = None, author: Optional[str] = None,
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    rows = build_blog_rows(db, current_user.id, _range(range, start_date, end_date, timezone_name), website_id, category, author)
    offset = (page - 1) * page_size
    return {"items": rows[offset:offset + page_size], "total": len(rows), "page": page, "page_size": page_size}


@router.get("/social-posts")
async def social_posts(
    range: str = Query("30d", pattern="^(today|7d|30d|90d|custom)$"),
    start_date: Optional[date] = None, end_date: Optional[date] = None,
    timezone_name: str = Query("UTC", alias="timezone"), platform: Optional[str] = None,
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    rows = build_social_rows(db, current_user.id, _range(range, start_date, end_date, timezone_name), platform)
    offset = (page - 1) * page_size
    return {"items": rows[offset:offset + page_size], "total": len(rows), "page": page, "page_size": page_size}


@router.get("/keyword-performance")
async def keywords(
    range: str = Query("30d", pattern="^(today|7d|30d|90d|custom)$"),
    start_date: Optional[date] = None, end_date: Optional[date] = None,
    timezone_name: str = Query("UTC", alias="timezone"), website_id: Optional[int] = None,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    return {"items": build_keyword_performance(db, current_user.id, _range(range, start_date, end_date, timezone_name), website_id)}


@router.get("/publishing-activity")
async def publishing_activity(
    outcome: Optional[Literal["success", "failed"]] = None,
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    blog_q = db.query(BlogPublicationAttempt).filter(BlogPublicationAttempt.user_id == current_user.id)
    social_q = db.query(SocialPublicationAttempt).filter(SocialPublicationAttempt.user_id == current_user.id)
    if outcome:
        blog_q = blog_q.filter(BlogPublicationAttempt.outcome == outcome)
        social_q = social_q.filter(SocialPublicationAttempt.outcome == outcome)
    items = [{
        "id": f"blog-{row.id}", "type": "blog", "destination": f"WordPress site #{row.wordpress_site_id}",
        "status": row.outcome, "date": row.attempted_at.isoformat(), "error": row.error_message,
    } for row in blog_q.order_by(BlogPublicationAttempt.attempted_at.desc()).limit(limit).all()]
    items += [{
        "id": f"social-{row.id}", "type": "social", "destination": row.platform,
        "status": row.outcome, "date": row.attempted_at.isoformat(), "error": row.error_message,
    } for row in social_q.order_by(SocialPublicationAttempt.attempted_at.desc()).limit(limit).all()]
    return {"items": sorted(items, key=lambda item: item["date"], reverse=True)[:limit]}


@router.get("/filters")
async def filters(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return build_filters(db, current_user.id)


@router.get("/published-blog-options")
async def published_blog_options(
    limit: int = Query(200, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(PublishedPost).join(
        WordPressSite, WordPressSite.id == PublishedPost.wordpress_site_id,
    ).filter(
        PublishedPost.user_id == current_user.id,
        WordPressSite.user_id == current_user.id,
        PublishedPost.wp_status == "publish",
    ).order_by(PublishedPost.published_at.desc()).limit(limit).all()

    tracked_by_site: dict[int, list] = {}
    for row in rows:
        tracked_by_site.setdefault(row.wordpress_site_id, []).append({
            "id": row.id,
            "wp_post_id": row.wp_post_id,
            "site_id": row.wordpress_site_id,
            "title": row.title,
            "website": row.wordpress_site.name,
            "url": row.canonical_url or row.wp_link,
            "published_at": (row.wordpress_published_at or row.published_at).isoformat(),
        })

    # published_posts only records what this app published. A site connected
    # with existing content has no rows, so also ask each site what it has.
    sites = db.query(WordPressSite).filter(WordPressSite.user_id == current_user.id).all()
    items: list[dict] = []
    for site in sites:
        tracked = tracked_by_site.get(site.id, [])
        try:
            live = await list_published_content(site, site.url)
        except Exception as exc:
            logger.info("could not list content for site %s: %s", site.id, exc)
            live = []
        items.extend(merge_options(tracked, live, site_id=site.id, website=site.name))

    # Sites that were deleted may still have tracked rows; keep those too.
    known_sites = {site.id for site in sites}
    for site_id, tracked in tracked_by_site.items():
        if site_id not in known_sites:
            items.extend(tracked)

    return {"items": items[:limit]}


@router.get("/integrations")
async def integrations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    gsc = db.query(GscProperty).filter(GscProperty.user_id == current_user.id).all()
    ga_token = db.query(GaOAuthToken).filter(GaOAuthToken.user_id == current_user.id).first()
    ga = db.query(GaProperty).filter(GaProperty.user_id == current_user.id).all()
    social = db.query(SocialAccount).filter(SocialAccount.user_id == current_user.id).all()
    return {
        "wordpress": {"connected": bool(build_filters(db, current_user.id)["websites"])},
        "gsc": {"connected": bool(gsc), "last_synced_at": max((row.last_synced_at for row in gsc if row.last_synced_at), default=None)},
        "ga": {"connected": bool(ga_token), "property_count": len(ga), "last_synced_at": max((row.last_synced_at for row in ga if row.last_synced_at), default=None)},
        "social": {"connected": bool(social), "platforms": [row.platform for row in social]},
    }


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh(
    background_tasks: BackgroundTasks,
    scope: Literal["all", "wordpress", "gsc", "ga", "social"] = "all",
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    existing = db.query(AnalyticsSyncRun).filter(
        AnalyticsSyncRun.user_id == current_user.id,
        AnalyticsSyncRun.status.in_(["queued", "running"]),
    ).order_by(AnalyticsSyncRun.created_at.desc()).first()
    if existing:
        return {"id": existing.id, "status": existing.status}
    run = AnalyticsSyncRun(user_id=current_user.id, trigger="manual", scope=scope, status="queued")
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(execute_sync_run, run.id, current_user.id)
    return {"id": run.id, "status": run.status}


@router.get("/sync-runs/{run_id}")
async def sync_run(run_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = db.query(AnalyticsSyncRun).filter(
        AnalyticsSyncRun.id == run_id, AnalyticsSyncRun.user_id == current_user.id
    ).first()
    if not run:
        raise HTTPException(status_code=404, detail="Sync run not found")
    return {
        "id": run.id, "status": run.status, "scope": run.scope, "result": run.result,
        "error": run.error_message, "created_at": run.created_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
