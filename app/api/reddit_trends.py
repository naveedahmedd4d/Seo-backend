from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, or_
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.keyword_site_coverage import KeywordSiteCoverage
from app.models.reddit_trends import RedditPost, RedditTrend, RedditTrendPost, RedditTrendRun
from app.models.trending_keywords import TrendingKeywords
from app.models.user import User
from app.schemas.reddit_trends import (
    RedditBulkActionRequest,
    RedditBulkActionResponse,
    RedditRunResponse,
    RedditRunStartRequest,
    RedditSourcePostResponse,
    RedditTrendListResponse,
    RedditTrendResponse,
    RedditTrendSettingsResponse,
    RedditTrendSettingsUpdate,
    RedditTrendStatusUpdate,
)
from app.services.keyword_coverage_service import (
    STATUS_EXISTING,
    STATUS_NEW,
    coverage_to_dict,
    get_site_for_user,
)
from app.services.reddit_trend_processing import ensure_aware, utcnow
from app.services.reddit_trends_service import (
    bg_execute_reddit_run,
    create_reddit_run,
    get_or_create_reddit_settings,
    latest_reddit_run,
    latest_successful_reddit_run,
    update_reddit_settings,
)

router = APIRouter()
ALLOWED_HOURS = (4, 12, 24, 168)
ALLOWED_SORTS = {
    "trend_score",
    "newest",
    "most_upvoted",
    "most_commented",
    "fastest_growing",
    "cross_subreddit",
}


def _run_response(run: RedditTrendRun, *, cached: bool = False, message: str = "") -> RedditRunResponse:
    return RedditRunResponse(
        id=run.id,
        status=run.status,
        stage=run.stage,
        trigger=run.trigger,
        force_refresh=run.force_refresh,
        items_collected=run.items_collected,
        posts_accepted=run.posts_accepted,
        clusters_created=run.clusters_created,
        error_message=run.error_message,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        cached=cached,
        message=message,
    )


def _settings_response(row) -> RedditTrendSettingsResponse:
    return RedditTrendSettingsResponse(
        enabled=row.enabled,
        interval_hours=row.interval_hours,
        queries=list(row.queries or []),
        subreddits=list(row.subreddits or []),
        keyword_max_posts=row.keyword_max_posts,
        subreddit_max_posts=row.subreddit_max_posts,
        daily_item_limit=row.daily_item_limit,
        monthly_item_limit=row.monthly_item_limit,
        freshness_hours=row.freshness_hours,
        next_run_at=row.next_run_at,
    )


def _trend_response(trend: RedditTrend, coverage=None) -> RedditTrendResponse:
    representative = trend.representative_post
    created = ensure_aware(representative.created_utc) if representative else None
    age = max(0.0, (utcnow() - created).total_seconds() / 3600) if created else None
    keyword = trend.keyword
    return RedditTrendResponse(
        id=trend.id,
        keyword_id=trend.trending_keyword_id,
        topic=trend.topic,
        keyword=keyword.keyword,
        summary=trend.summary,
        category=trend.category,
        topic_type=trend.topic_type,
        primary_subreddit=trend.primary_subreddit,
        subreddit_mentions=trend.subreddit_mentions,
        post_score=trend.post_score,
        comments=trend.comments,
        score_per_hour=trend.score_per_hour,
        comments_per_hour=trend.comments_per_hour,
        upvote_ratio=trend.upvote_ratio,
        trend_score=trend.trend_score,
        post_age_hours=age,
        source_url=representative.canonical_url if representative else None,
        timestamp=trend.latest_post_at,
        status=trend.status,
        converted=trend.converted_at is not None,
        kd=keyword.kd,
        blog_workflow_id=(
            keyword.blog_workflow_id
            or keyword.net_worth_blog_workflow_id
            or keyword.bio_blog_workflow_id
        ),
        coverage=coverage_to_dict(coverage),
    )


@router.get("/settings", response_model=RedditTrendSettingsResponse)
async def get_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _settings_response(get_or_create_reddit_settings(db, current_user.id))


@router.patch("/settings", response_model=RedditTrendSettingsResponse)
async def patch_settings(
    body: RedditTrendSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    values = body.model_dump(exclude_unset=True)
    if "subreddits" in values:
        values["subreddits"] = [item.model_dump() for item in body.subreddits or []]
    try:
        row = update_reddit_settings(db, current_user.id, values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _settings_response(row)


@router.post("/runs", response_model=RedditRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    body: RedditRunStartRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        run, cached = create_reddit_run(db, current_user.id, force=body.force)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    if cached:
        return _run_response(run, cached=True, message="Recent cached Reddit trends are still fresh.")
    if run.status == "pending":
        background_tasks.add_task(bg_execute_reddit_run, current_user.id, run.id)
        return _run_response(run, message="Reddit trend collection started.")
    return _run_response(run, message="A Reddit trend collection is already active.")


@router.get("/runs/latest", response_model=Optional[RedditRunResponse])
async def get_latest_run(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = latest_reddit_run(db, current_user.id)
    return _run_response(run) if run else None


@router.get("/runs/{run_id}", response_model=RedditRunResponse)
async def get_run(
    run_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = db.query(RedditTrendRun).filter(
        RedditTrendRun.id == run_id,
        RedditTrendRun.user_id == current_user.id,
    ).first()
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reddit trend run not found")
    return _run_response(run)


@router.get("/trends", response_model=RedditTrendListResponse)
async def list_trends(
    search: Optional[str] = None,
    category: Optional[str] = None,
    subreddit: Optional[str] = None,
    topic_type: Optional[str] = None,
    hours: int = 24,
    min_score: float = Query(0, ge=0, le=100),
    trend_status: str = "active",
    sort: str = "trend_score",
    wordpress_site_id: Optional[int] = None,
    coverage_status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if hours not in ALLOWED_HOURS:
        raise HTTPException(status_code=400, detail=f"hours must be one of {ALLOWED_HOURS}")
    if sort not in ALLOWED_SORTS:
        raise HTTPException(status_code=400, detail=f"sort must be one of {sorted(ALLOWED_SORTS)}")
    if trend_status not in ("active", "ignored", "archived", "all"):
        raise HTTPException(status_code=400, detail="Invalid trend status")
    if coverage_status and coverage_status not in (STATUS_EXISTING, STATUS_NEW):
        raise HTTPException(status_code=400, detail="Invalid coverage status")
    if coverage_status and wordpress_site_id is None:
        raise HTTPException(status_code=400, detail="wordpress_site_id is required for coverage filtering")

    query = (
        db.query(RedditTrend)
        .join(TrendingKeywords, TrendingKeywords.id == RedditTrend.trending_keyword_id)
        .filter(
            RedditTrend.user_id == current_user.id,
            RedditTrend.latest_post_at >= utcnow() - timedelta(hours=hours),
            RedditTrend.trend_score >= min_score,
        )
    )
    if trend_status != "all":
        query = query.filter(RedditTrend.status == trend_status)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(or_(RedditTrend.topic.ilike(term), RedditTrend.summary.ilike(term)))
    if category:
        query = query.filter(RedditTrend.category == category)
    if subreddit:
        query = query.filter(
            RedditTrend.posts.any(
                RedditTrendPost.post.has(RedditPost.subreddit.ilike(subreddit.strip()))
            )
        )
    if topic_type:
        query = query.filter(RedditTrend.topic_type == topic_type)
    if wordpress_site_id is not None:
        get_site_for_user(db, current_user, wordpress_site_id)
        if coverage_status:
            query = query.join(
                KeywordSiteCoverage,
                (KeywordSiteCoverage.trending_keyword_id == RedditTrend.trending_keyword_id)
                & (KeywordSiteCoverage.wordpress_site_id == wordpress_site_id),
            ).filter(KeywordSiteCoverage.status == coverage_status)

    order_map = {
        "trend_score": desc(RedditTrend.trend_score),
        "newest": desc(RedditTrend.latest_post_at),
        "most_upvoted": desc(RedditTrend.post_score),
        "most_commented": desc(RedditTrend.comments),
        "fastest_growing": desc(RedditTrend.score_per_hour + RedditTrend.comments_per_hour),
        "cross_subreddit": desc(RedditTrend.subreddit_mentions),
    }
    count = query.count()
    rows = query.order_by(order_map[sort], asc(RedditTrend.id)).offset((page - 1) * page_size).limit(page_size).all()
    coverage_map = {}
    if wordpress_site_id is not None and rows:
        coverage_rows = db.query(KeywordSiteCoverage).filter(
            KeywordSiteCoverage.wordpress_site_id == wordpress_site_id,
            KeywordSiteCoverage.trending_keyword_id.in_([row.trending_keyword_id for row in rows]),
        ).all()
        coverage_map = {row.trending_keyword_id: row for row in coverage_rows}

    last_run = latest_reddit_run(db, current_user.id)
    last_success = latest_successful_reddit_run(db, current_user.id)
    return RedditTrendListResponse(
        trends=[_trend_response(row, coverage_map.get(row.trending_keyword_id)) for row in rows],
        count=count,
        page=page,
        page_size=page_size,
        last_run=_run_response(last_run) if last_run else None,
        last_successful_at=last_success.completed_at if last_success else None,
    )


def _get_user_trend(db: Session, user_id: int, trend_id: int) -> RedditTrend:
    trend = db.query(RedditTrend).filter(
        RedditTrend.id == trend_id,
        RedditTrend.user_id == user_id,
    ).first()
    if not trend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reddit trend not found")
    return trend


@router.get("/trends/{trend_id}/sources", response_model=list[RedditSourcePostResponse])
async def trend_sources(
    trend_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trend = _get_user_trend(db, current_user.id, trend_id)
    posts = (
        db.query(RedditPost)
        .join(RedditTrendPost, RedditTrendPost.reddit_post_id == RedditPost.id)
        .filter(RedditTrendPost.reddit_trend_id == trend.id, RedditPost.user_id == current_user.id)
        .order_by(RedditPost.score_per_hour.desc())
        .all()
    )
    return [
        RedditSourcePostResponse(
            id=post.id,
            reddit_post_id=post.reddit_post_id,
            title=post.title,
            canonical_url=post.canonical_url,
            subreddit=post.subreddit,
            author=post.author,
            score=post.score,
            comments=post.comments,
            score_per_hour=post.score_per_hour,
            comments_per_hour=post.comments_per_hour,
            upvote_ratio=post.upvote_ratio,
            created_utc=post.created_utc,
            retrieved_at=post.retrieved_at,
        )
        for post in posts
    ]


@router.post("/trends/{trend_id}/convert", response_model=RedditTrendResponse)
async def convert_trend(
    trend_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trend = _get_user_trend(db, current_user.id, trend_id)
    trend.converted_at = trend.converted_at or utcnow()
    db.commit()
    db.refresh(trend)
    return _trend_response(trend)


@router.patch("/trends/{trend_id}", response_model=RedditTrendResponse)
async def update_trend_status(
    trend_id: int,
    body: RedditTrendStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trend = _get_user_trend(db, current_user.id, trend_id)
    trend.status = body.status
    db.commit()
    db.refresh(trend)
    return _trend_response(trend)


@router.post("/bulk-actions", response_model=RedditBulkActionResponse)
async def bulk_action(
    body: RedditBulkActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(RedditTrend).filter(
        RedditTrend.user_id == current_user.id,
        RedditTrend.id.in_(body.trend_ids),
    ).all()
    now = utcnow()
    for row in rows:
        if body.action == "convert":
            row.converted_at = row.converted_at or now
        elif body.action == "restore":
            row.status = "active"
        else:
            row.status = body.action
    db.commit()
    return RedditBulkActionResponse(updated=len(rows), message=f"Updated {len(rows)} Reddit trend(s).")
