"""Reddit Trends run orchestration and persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models.reddit_trends import (
    RedditActorRun,
    RedditPost,
    RedditTrend,
    RedditTrendPost,
    RedditTrendRun,
    RedditTrendSettings,
)
from app.models.trending_keywords import TrendingKeywords
from app.services.apify_reddit_service import ApifyRedditProvider
from app.services.llm_settings_service import resolve_api_key, resolve_llm_credentials
from app.services.reddit_trend_processing import (
    aggregate_clusters,
    cluster_posts,
    deduplicate_posts,
    enrich_clusters_with_llm,
    ensure_aware,
    utcnow,
    validate_post_item,
)

logger = logging.getLogger(__name__)

SOURCE_REDDIT = "reddit"
ACTIVE_RUN_STATUSES = ("pending", "running")

DEFAULT_QUERIES = [
    "artificial intelligence",
    "generative AI",
    "AI agents",
    "large language models",
    "machine learning",
    "OpenAI",
    "Anthropic",
    "Google Gemini",
    "NVIDIA AI",
]

DEFAULT_SUBREDDITS = [
    {"name": name, "keywords": ["AI", "artificial intelligence", "LLM", "machine learning"], "enabled": True}
    for name in (
        "technology",
        "artificial",
        "MachineLearning",
        "LocalLLaMA",
        "singularity",
        "OpenAI",
        "ChatGPT",
        "ClaudeAI",
        "programming",
        "technews",
    )
]


def get_or_create_reddit_settings(db: Session, user_id: int) -> RedditTrendSettings:
    row = db.query(RedditTrendSettings).filter(RedditTrendSettings.user_id == user_id).first()
    if row:
        return row
    row = RedditTrendSettings(
        user_id=user_id,
        enabled=True,
        interval_hours=12,
        queries=list(DEFAULT_QUERIES),
        subreddits=[dict(item) for item in DEFAULT_SUBREDDITS],
        keyword_max_posts=25,
        subreddit_max_posts=30,
        daily_item_limit=1100,
        monthly_item_limit=32000,
        freshness_hours=12,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_reddit_settings(db: Session, user_id: int, values: Dict[str, Any]) -> RedditTrendSettings:
    row = get_or_create_reddit_settings(db, user_id)
    queries = values.get("queries", row.queries) or []
    subreddits = values.get("subreddits", row.subreddits) or []
    has_queries = any(str(query).strip() for query in queries)
    has_subreddits = any(
        isinstance(item, dict)
        and item.get("enabled", True)
        and str(item.get("name") or "").strip()
        for item in subreddits
    )
    if not has_queries and not has_subreddits:
        raise ValueError("Configure at least one keyword query or enabled subreddit.")
    for key, value in values.items():
        if value is not None and hasattr(row, key):
            setattr(row, key, value)
    if "interval_hours" in values:
        row.next_run_at = utcnow() + timedelta(hours=int(row.interval_hours))
    db.commit()
    db.refresh(row)
    return row


def _enabled_subreddits(config: RedditTrendSettings) -> List[Dict[str, Any]]:
    return [
        item
        for item in (config.subreddits or [])
        if isinstance(item, dict) and item.get("enabled", True) and str(item.get("name") or "").strip()
    ]


def _estimated_max_items(config: RedditTrendSettings) -> int:
    query_count = len([q for q in (config.queries or []) if str(q).strip()])
    keyword_total = query_count * min(50, max(1, int(config.keyword_max_posts or 25)))
    subreddit_total = sum(
        min(50, max(1, int(item.get("max_posts") or config.subreddit_max_posts or 30)))
        for item in _enabled_subreddits(config)
    )
    return keyword_total + subreddit_total


def _period_usage(db: Session, user_id: int, since: datetime) -> int:
    value = (
        db.query(func.coalesce(func.sum(RedditTrendRun.items_collected), 0))
        .filter(
            RedditTrendRun.user_id == user_id,
            RedditTrendRun.created_at >= since,
            RedditTrendRun.items_collected > 0,
        )
        .scalar()
    )
    return int(value or 0)


def latest_reddit_run(db: Session, user_id: int) -> Optional[RedditTrendRun]:
    return (
        db.query(RedditTrendRun)
        .filter(RedditTrendRun.user_id == user_id)
        .order_by(RedditTrendRun.created_at.desc())
        .first()
    )


def latest_successful_reddit_run(db: Session, user_id: int) -> Optional[RedditTrendRun]:
    return (
        db.query(RedditTrendRun)
        .filter(
            RedditTrendRun.user_id == user_id,
            RedditTrendRun.status.in_(("succeeded", "partial")),
        )
        .order_by(RedditTrendRun.completed_at.desc())
        .first()
    )


def create_reddit_run(
    db: Session,
    user_id: int,
    *,
    force: bool = False,
    trigger: str = "manual",
) -> Tuple[RedditTrendRun, bool]:
    config = get_or_create_reddit_settings(db, user_id)
    active = (
        db.query(RedditTrendRun)
        .filter(
            RedditTrendRun.user_id == user_id,
            RedditTrendRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .order_by(RedditTrendRun.created_at.desc())
        .first()
    )
    if active:
        scope_count = (1 if any(str(query).strip() for query in (config.queries or [])) else 0) + len(
            _enabled_subreddits(config)
        )
        stale_after = timedelta(
            seconds=max(900, int(app_settings.reddit_trends_run_timeout_seconds) * max(1, scope_count) + 300)
        )
        active_since = ensure_aware(active.started_at or active.created_at)
        if active_since and active_since < utcnow() - stale_after:
            active.status = "failed"
            active.stage = "failed"
            active.error_message = "The previous Reddit collection became stale and was released."
            active.completed_at = utcnow()
            db.commit()
            active = None
    if active:
        return active, False

    latest_success = (
        db.query(RedditTrendRun)
        .filter(
            RedditTrendRun.user_id == user_id,
            RedditTrendRun.status.in_(("succeeded", "partial")),
        )
        .order_by(RedditTrendRun.completed_at.desc())
        .first()
    )
    freshness_cutoff = utcnow() - timedelta(hours=max(1, int(config.freshness_hours or 12)))
    if (
        not force
        and latest_success
        and ensure_aware(latest_success.completed_at)
        and ensure_aware(latest_success.completed_at) >= freshness_cutoff
    ):
        return latest_success, True

    estimated = _estimated_max_items(config)
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    daily_used = _period_usage(db, user_id, day_start)
    monthly_used = _period_usage(db, user_id, month_start)
    if daily_used + estimated > int(config.daily_item_limit):
        raise ValueError(
            f"Reddit daily item limit would be exceeded ({daily_used}/{config.daily_item_limit} used)."
        )
    if monthly_used + estimated > int(config.monthly_item_limit):
        raise ValueError(
            f"Reddit monthly item limit would be exceeded ({monthly_used}/{config.monthly_item_limit} used)."
        )

    snapshot = {
        "queries": list(config.queries or []),
        "subreddits": list(config.subreddits or []),
        "keyword_max_posts": config.keyword_max_posts,
        "subreddit_max_posts": config.subreddit_max_posts,
        "interval_hours": config.interval_hours,
    }
    run = RedditTrendRun(
        user_id=user_id,
        trigger=trigger,
        status="pending",
        stage="queued",
        force_refresh=force,
        actor_id=app_settings.apify_reddit_actor_id,
        estimated_max_items=estimated,
        input_snapshot=snapshot,
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        active = (
            db.query(RedditTrendRun)
            .filter(
                RedditTrendRun.user_id == user_id,
                RedditTrendRun.status.in_(ACTIVE_RUN_STATUSES),
            )
            .order_by(RedditTrendRun.created_at.desc())
            .first()
        )
        if active:
            return active, False
        raise
    db.refresh(run)
    return run, False


def _keyword_input(config: RedditTrendSettings) -> Dict[str, Any]:
    return {
        "queries": [str(q).strip() for q in (config.queries or []) if str(q).strip()],
        "sort": "new",
        "timeframe": "day",
        "scrapeComments": False,
        "sentiment_analysis": False,
        "content_analysis": False,
        "maximize_coverage": False,
        "strictSearch": True,
        "strictTokenFilter": False,
        "includeNsfw": False,
        "maxPosts": min(50, max(1, int(config.keyword_max_posts or 25))),
    }


def _subreddit_input(config: RedditTrendSettings, item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "subredditName": str(item.get("name") or "").strip().removeprefix("r/"),
        "subredditKeywords": [str(k).strip() for k in (item.get("keywords") or []) if str(k).strip()],
        "subredditSort": "new",
        "subredditTimeframe": "day",
        "scrapeComments": False,
        "sentiment_analysis": False,
        "content_analysis": False,
        "maximize_coverage": False,
        "includeNsfw": False,
        "maxPosts": min(50, max(1, int(item.get("max_posts") or config.subreddit_max_posts or 30))),
    }


async def _collect_scope(
    db: Session,
    provider: ApifyRedditProvider,
    parent_run: RedditTrendRun,
    scope_type: str,
    scope_key: str,
    run_input: Dict[str, Any],
    hard_limit: int,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    actor_row = RedditActorRun(
        reddit_trend_run_id=parent_run.id,
        scope_type=scope_type,
        scope_key=scope_key,
        status="starting",
    )
    db.add(actor_row)
    db.commit()
    db.refresh(actor_row)
    try:
        handle = await provider.start_run(run_input)
        actor_row.apify_run_id = handle.run_id
        actor_row.dataset_id = handle.dataset_id
        actor_row.status = "running"
        db.commit()
        await provider.wait_for_run(handle.run_id)
        items = await provider.fetch_dataset(handle.dataset_id, hard_limit)
        actor_row.status = "succeeded"
        actor_row.item_count = len(items)
        actor_row.completed_at = utcnow()
        db.commit()
        logger.info(
            "Reddit Actor run %s scope=%s/%s retrieved %s dataset items",
            handle.run_id,
            scope_type,
            scope_key,
            len(items),
        )
        return items, None
    except Exception as exc:
        logger.warning("Reddit Actor scope %s/%s failed: %s", scope_type, scope_key, exc)
        actor_row.status = "failed"
        actor_row.error_message = str(exc)[:1000]
        actor_row.completed_at = utcnow()
        db.commit()
        return [], f"{scope_type}:{scope_key}"


def _upsert_posts(db: Session, user_id: int, posts: List[Dict[str, Any]]) -> Dict[str, RedditPost]:
    ids = [p["reddit_post_id"] for p in posts]
    urls = [p["canonical_url"] for p in posts]
    rows = (
        db.query(RedditPost)
        .filter(
            RedditPost.user_id == user_id,
            or_(RedditPost.reddit_post_id.in_(ids), RedditPost.canonical_url.in_(urls)),
        )
        .all()
    ) if posts else []
    by_id = {row.reddit_post_id: row for row in rows}
    by_url = {row.canonical_url: row for row in rows}
    result: Dict[str, RedditPost] = {}
    now = utcnow()
    for post in posts:
        row = by_id.get(post["reddit_post_id"]) or by_url.get(post["canonical_url"])
        if row is None:
            row = RedditPost(user_id=user_id, **post)
            db.add(row)
        else:
            for key, value in post.items():
                setattr(row, key, value)
            row.last_seen_at = now
        by_id[post["reddit_post_id"]] = row
        by_url[post["canonical_url"]] = row
        result[post["reddit_post_id"]] = row
    db.flush()
    return result


def _persist_clusters(
    db: Session,
    user_id: int,
    run: RedditTrendRun,
    aggregates: List[Dict[str, Any]],
    post_rows: Dict[str, RedditPost],
) -> int:
    now = utcnow()
    created = 0
    for data in aggregates:
        trend = (
            db.query(RedditTrend)
            .filter(
                RedditTrend.user_id == user_id,
                RedditTrend.fingerprint == data["fingerprint"],
            )
            .first()
        )
        if trend is None:
            keyword_row = TrendingKeywords(
                user_id=user_id,
                keyword=data["topic"],
                category=data["category"],
                source=SOURCE_REDDIT,
                fetched_at=now,
                data={},
            )
            db.add(keyword_row)
            db.flush()
            trend = RedditTrend(
                user_id=user_id,
                trending_keyword_id=keyword_row.id,
                fingerprint=data["fingerprint"],
                topic=data["topic"],
                first_seen_at=now,
            )
            db.add(trend)
            db.flush()
            created += 1
        else:
            keyword_row = trend.keyword
        keyword_row.keyword = data["topic"]
        keyword_row.category = data["category"]
        keyword_row.fetched_at = now
        existing_data = dict(keyword_row.data) if isinstance(keyword_row.data, dict) else {}
        existing_data.update({
            "timestamp": data["latest_post_at"].isoformat(),
            "value": data["post_score"],
            "reddit_trend_score": data["trend_score"],
        })
        keyword_row.data = existing_data

        representative = post_rows[data["representative"]["reddit_post_id"]]
        trend.latest_run_id = run.id
        trend.representative_post_id = representative.id
        for field in (
            "topic",
            "summary",
            "category",
            "topic_type",
            "primary_subreddit",
            "subreddit_mentions",
            "post_score",
            "comments",
            "score_per_hour",
            "comments_per_hour",
            "upvote_ratio",
            "engagement_total",
            "normalized_score_per_hour",
            "normalized_comments_per_hour",
            "normalized_upvote_ratio",
            "normalized_cross_subreddit",
            "trend_score",
            "latest_post_at",
        ):
            setattr(trend, field, data[field])
        trend.last_seen_at = now

        linked_ids = {
            link.reddit_post_id
            for link in db.query(RedditTrendPost)
            .filter(RedditTrendPost.reddit_trend_id == trend.id)
            .all()
        }
        for post in data["members"]:
            post_row = post_rows[post["reddit_post_id"]]
            if post_row.id in linked_ids:
                link = (
                    db.query(RedditTrendPost)
                    .filter(
                        RedditTrendPost.reddit_trend_id == trend.id,
                        RedditTrendPost.reddit_post_id == post_row.id,
                    )
                    .first()
                )
                if link:
                    link.last_seen_run_id = run.id
            else:
                db.add(RedditTrendPost(
                    reddit_trend_id=trend.id,
                    reddit_post_id=post_row.id,
                    first_seen_run_id=run.id,
                    last_seen_run_id=run.id,
                ))
    db.commit()
    return created


async def execute_reddit_run(db: Session, user_id: int, run_id: int) -> None:
    run = (
        db.query(RedditTrendRun)
        .filter(RedditTrendRun.id == run_id, RedditTrendRun.user_id == user_id)
        .first()
    )
    if not run or run.status not in ACTIVE_RUN_STATUSES:
        return
    config = get_or_create_reddit_settings(db, user_id)
    try:
        run.status = "running"
        run.stage = "collecting"
        run.started_at = utcnow()
        db.commit()

        token = resolve_api_key(db, user_id, "apify")
        provider = ApifyRedditProvider(token, run.actor_id)
        all_items: List[Dict[str, Any]] = []
        failures: List[str] = []

        keyword_input = _keyword_input(config)
        if keyword_input["queries"]:
            hard_limit = len(keyword_input["queries"]) * int(keyword_input["maxPosts"])
            items, failure = await _collect_scope(
                db, provider, run, "keywords", "all", keyword_input, hard_limit
            )
            all_items.extend(items)
            if failure:
                failures.append(failure)

        for subreddit in _enabled_subreddits(config):
            subreddit_input = _subreddit_input(config, subreddit)
            items, failure = await _collect_scope(
                db,
                provider,
                run,
                "subreddit",
                subreddit_input["subredditName"],
                subreddit_input,
                int(subreddit_input["maxPosts"]),
            )
            all_items.extend(items)
            if failure:
                failures.append(failure)

        run.items_collected = len(all_items)
        run.stage = "normalizing"
        db.commit()
        validated = [post for item in all_items if (post := validate_post_item(item))]
        posts = deduplicate_posts(validated)
        run.posts_accepted = len(posts)
        if not posts:
            raise RuntimeError("No valid Reddit posts were collected")

        post_rows = _upsert_posts(db, user_id, posts)
        db.commit()
        run.stage = "clustering"
        db.commit()
        deterministic = cluster_posts(posts)
        try:
            llm_credentials = resolve_llm_credentials(db, user_id)
        except ValueError:
            llm_credentials = None
        enriched = await enrich_clusters_with_llm(deterministic, llm_credentials)
        aggregates, normalization = aggregate_clusters(enriched, len(_enabled_subreddits(config)))

        run.stage = "saving"
        run.normalization_stats = normalization
        db.commit()
        _persist_clusters(db, user_id, run, aggregates, post_rows)
        run.clusters_created = len(aggregates)
        run.status = "partial" if failures else "succeeded"
        run.stage = "completed"
        run.error_message = (
            f"Some collection scopes failed: {', '.join(failures[:8])}" if failures else None
        )
        run.completed_at = utcnow()
        config.next_run_at = utcnow() + timedelta(hours=int(config.interval_hours or 12))
        db.commit()
    except Exception as exc:
        logger.exception("Reddit trend run %s failed", run_id)
        db.rollback()
        run = db.query(RedditTrendRun).filter(RedditTrendRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.stage = "failed"
            run.error_message = str(exc)[:1000]
            run.completed_at = utcnow()
            db.commit()


async def bg_execute_reddit_run(user_id: int, run_id: int) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        await execute_reddit_run(db, user_id, run_id)
    finally:
        db.close()


async def run_due_reddit_jobs() -> int:
    """Run due account configurations. Intended for a Render Cron Job."""
    from app.database import SessionLocal

    db = SessionLocal()
    started = 0
    try:
        due = (
            db.query(RedditTrendSettings)
            .filter(
                RedditTrendSettings.enabled.is_(True),
                (RedditTrendSettings.next_run_at.is_(None))
                | (RedditTrendSettings.next_run_at <= utcnow()),
            )
            .all()
        )
        for config in due:
            try:
                run, cached = create_reddit_run(db, config.user_id, trigger="scheduled")
                if cached or run.status not in ACTIVE_RUN_STATUSES:
                    config.next_run_at = utcnow() + timedelta(hours=int(config.interval_hours or 12))
                    db.commit()
                    continue
                await execute_reddit_run(db, config.user_id, run.id)
                started += 1
            except Exception as exc:
                logger.warning("Could not schedule Reddit trends for user %s: %s", config.user_id, exc)
        return started
    finally:
        db.close()
