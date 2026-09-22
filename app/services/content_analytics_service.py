from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.content_analytics import (
    BlogPublicationAttempt,
    GaPageMetric,
    GaProperty,
    SocialAccountMetricSnapshot,
    SocialPostMetricSnapshot,
    SocialPublicationAttempt,
)
from app.models.gsc_page_metric import GscPageMetric
from app.models.gsc_property import GscProperty
from app.models.gsc_query_metric import GscQueryMetric
from app.models.published_post import PublishedPost
from app.models.social_post_history import SocialPostHistory
from app.models.social_post_publication import SocialPostPublication
from app.models.wordpress_site import WordPressSite
from app.models.workflow_state import WorkflowState


SOCIAL_METRICS = (
    "impressions", "reach", "likes", "reactions", "comments", "shares", "reposts", "link_clicks"
)


@dataclass(frozen=True)
class AnalyticsRange:
    start: date
    end: date
    timezone_name: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def resolve_range(
    range_key: str,
    start_date: date | None,
    end_date: date | None,
    timezone_name: str,
) -> AnalyticsRange:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
        timezone_name = "UTC"
    today = datetime.now(zone).date()
    if range_key == "today":
        start = end = today
    elif range_key == "7d":
        end, start = today, today - timedelta(days=6)
    elif range_key == "90d":
        end, start = today, today - timedelta(days=89)
    elif range_key == "custom" and start_date and end_date:
        start, end = start_date, end_date
    else:
        end, start = today, today - timedelta(days=29)
    if start > end:
        start, end = end, start
    if (end - start).days > 550:
        start = end - timedelta(days=550)
    return AnalyticsRange(start=start, end=end, timezone_name=timezone_name)


def normalize_url(value: str | None) -> str:
    if not value:
        return ""
    raw = value.strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://placeholder.invalid{raw if raw.startswith('/') else '/' + raw}")
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "placeholder.invalid":
        host = ""
    path = parsed.path or "/"
    path = "/" + path.lstrip("/")
    path = path.rstrip("/") or "/"
    return f"{host}{path}" if host else path


def _path_only(value: str | None) -> str:
    normalized = normalize_url(value)
    if not normalized:
        return ""
    slash = normalized.find("/")
    return normalized[slash:] if slash >= 0 else "/"


def _safe_zone(name: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _date_in_zone(value: datetime | None, zone_name: str) -> date | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_safe_zone(zone_name)).date()


def _workflow_metadata(workflow: WorkflowState | None, blog_key: str) -> tuple[str | None, str | None]:
    data = workflow.state_data if workflow and isinstance(workflow.state_data, dict) else {}
    article: dict[str, Any] = data
    if data.get("is_tree"):
        if blog_key == "parent":
            article = data.get("parent") or data
        elif blog_key.startswith("child_"):
            try:
                article = (data.get("children") or [])[int(blog_key.split("_", 1)[1])]
            except (IndexError, TypeError, ValueError):
                article = data
    keyword = article.get("focus_keyword") or data.get("focus_keyword") or data.get("topic")
    category = data.get("category") or article.get("category")
    return str(keyword).strip() if keyword else None, str(category).strip() if category else None


def _metric(value: int | float | None, availability: str = "available") -> dict[str, Any]:
    return {"value": value, "availability": availability if value is not None else availability}


def _pct_change(current: float, prior: float) -> float | None:
    if prior == 0:
        return 100.0 if current > 0 else 0.0
    return round((current - prior) / prior * 100, 1)


def _published_posts(
    db: Session,
    user_id: int,
    analytics_range: AnalyticsRange,
    website_id: int | None,
    category: str | None = None,
    author: str | None = None,
) -> list[PublishedPost]:
    query = (
        db.query(PublishedPost)
        .join(WordPressSite, WordPressSite.id == PublishedPost.wordpress_site_id)
        .filter(
            PublishedPost.user_id == user_id,
            WordPressSite.user_id == user_id,
            PublishedPost.wp_status == "publish",
        )
    )
    if website_id:
        query = query.filter(PublishedPost.wordpress_site_id == website_id)
    if category:
        query = query.filter(PublishedPost.source_category == category)
    if author:
        query = query.filter(PublishedPost.wp_author_name == author)
    rows = query.order_by(PublishedPost.published_at.desc()).all()
    return [
        row for row in rows
        if (dt := _date_in_zone(row.wordpress_published_at or row.published_at, analytics_range.timezone_name))
        and analytics_range.start <= dt <= analytics_range.end
    ]


def _gsc_by_url(
    db: Session,
    user_id: int,
    analytics_range: AnalyticsRange,
    website_id: int | None,
) -> dict[str, dict[str, float]]:
    query = (
        db.query(GscPageMetric)
        .join(GscProperty, GscProperty.id == GscPageMetric.property_id)
        .filter(
            GscProperty.user_id == user_id,
            GscPageMetric.date >= analytics_range.start,
            GscPageMetric.date <= analytics_range.end,
        )
    )
    if website_id:
        query = query.filter(GscProperty.wordpress_site_id == website_id)
    out: dict[str, dict[str, float]] = defaultdict(lambda: {"clicks": 0, "impressions": 0, "weighted_position": 0.0})
    for row in query.all():
        key = normalize_url(row.page)
        out[key]["clicks"] += row.clicks or 0
        out[key]["impressions"] += row.impressions or 0
        out[key]["weighted_position"] += (row.position or 0.0) * (row.impressions or 0)
    return out


def _ga_by_path(
    db: Session,
    user_id: int,
    analytics_range: AnalyticsRange,
    website_id: int | None,
) -> dict[str, dict[str, int]]:
    query = (
        db.query(GaPageMetric)
        .join(GaProperty, GaProperty.id == GaPageMetric.property_id)
        .filter(
            GaProperty.user_id == user_id,
            GaPageMetric.date >= analytics_range.start,
            GaPageMetric.date <= analytics_range.end,
        )
    )
    if website_id:
        query = query.filter(GaProperty.wordpress_site_id == website_id)
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"views": 0, "sessions": 0, "organic_sessions": 0})
    for row in query.all():
        key = _path_only(row.page_path)
        out[key]["views"] += row.views or 0
        out[key]["sessions"] += row.sessions or 0
        out[key]["organic_sessions"] += row.organic_sessions or 0
    return out


def _related_social_counts(db: Session, user_id: int, post_ids: Iterable[int]) -> dict[int, int]:
    ids = list(post_ids)
    if not ids:
        return {}
    rows = (
        db.query(SocialPostHistory.related_published_post_id, func.count(SocialPostPublication.id))
        .join(SocialPostPublication, SocialPostPublication.history_id == SocialPostHistory.id)
        .filter(
            SocialPostHistory.user_id == user_id,
            SocialPostHistory.related_published_post_id.in_(ids),
            SocialPostPublication.status == "success",
        )
        .group_by(SocialPostHistory.related_published_post_id)
        .all()
    )
    return {int(row[0]): int(row[1]) for row in rows}


def build_blog_rows(
    db: Session,
    user_id: int,
    analytics_range: AnalyticsRange,
    website_id: int | None = None,
    category: str | None = None,
    author: str | None = None,
) -> list[dict[str, Any]]:
    posts = _published_posts(db, user_id, analytics_range, website_id, category, author)
    gsc = _gsc_by_url(db, user_id, analytics_range, website_id)
    ga = _ga_by_path(db, user_id, analytics_range, website_id)
    related = _related_social_counts(db, user_id, [post.id for post in posts])
    workflow_ids = {post.workflow_id for post in posts}
    workflows = {
        row.id: row for row in db.query(WorkflowState).filter(
            WorkflowState.user_id == user_id, WorkflowState.id.in_(workflow_ids)
        ).all()
    } if workflow_ids else {}
    rows: list[dict[str, Any]] = []
    for post in posts:
        keyword, workflow_category = _workflow_metadata(workflows.get(post.workflow_id), post.blog_key)
        url = post.canonical_url or post.wp_link
        search = gsc.get(normalize_url(url), {})
        traffic = ga.get(_path_only(url), {})
        impressions = int(search.get("impressions", 0))
        clicks = int(search.get("clicks", 0))
        views = traffic.get("views")
        rows.append({
            "id": post.id,
            "title": post.title or "Untitled article",
            "website_id": post.wordpress_site_id,
            "website": post.wordpress_site.name if post.wordpress_site else "Unknown website",
            "target_keyword": post.target_keyword or keyword,
            "category": post.source_category or workflow_category,
            "author": post.wp_author_name,
            "publication_date": (post.wordpress_published_at or post.published_at).isoformat() if (post.wordpress_published_at or post.published_at) else None,
            "status": "published",
            "views": _metric(int(views) if views is not None else None, "not_connected" if views is None else "available"),
            "organic_traffic": _metric(int(traffic.get("organic_sessions")) if traffic else None, "not_connected" if not traffic else "available"),
            "impressions": _metric(impressions),
            "clicks": _metric(clicks),
            "ctr": _metric(round(clicks / impressions, 4) if impressions else 0.0),
            "average_position": _metric(round(float(search.get("weighted_position", 0.0)) / impressions, 2) if impressions else None, "pending" if not impressions else "available"),
            "wordpress_url": post.canonical_url or post.wp_link,
            "related_social_posts": related.get(post.id, 0),
        })
    return rows


def _successful_social_publications(
    db: Session,
    user_id: int,
    analytics_range: AnalyticsRange,
    platform: str | None,
) -> list[SocialPostPublication]:
    query = (
        db.query(SocialPostPublication)
        .join(SocialPostHistory, SocialPostHistory.id == SocialPostPublication.history_id)
        .filter(
            SocialPostPublication.user_id == user_id,
            SocialPostHistory.user_id == user_id,
            SocialPostPublication.status == "success",
        )
    )
    if platform:
        query = query.filter(SocialPostPublication.platform == platform)
    return [
        row for row in query.order_by(SocialPostPublication.published_at.desc()).all()
        if (dt := _date_in_zone(row.published_at, analytics_range.timezone_name))
        and analytics_range.start <= dt <= analytics_range.end
    ]


def _social_metric_deltas(
    db: Session,
    user_id: int,
    publication_ids: list[int],
    analytics_range: AnalyticsRange,
) -> dict[int, dict[str, int | None]]:
    if not publication_ids:
        return {}
    snapshots = (
        db.query(SocialPostMetricSnapshot)
        .filter(
            SocialPostMetricSnapshot.user_id == user_id,
            SocialPostMetricSnapshot.publication_id.in_(publication_ids),
            SocialPostMetricSnapshot.snapshot_date <= analytics_range.end,
        )
        .order_by(SocialPostMetricSnapshot.publication_id, SocialPostMetricSnapshot.snapshot_date)
        .all()
    )
    by_pub: dict[int, list[SocialPostMetricSnapshot]] = defaultdict(list)
    for snap in snapshots:
        by_pub[snap.publication_id].append(snap)
    out: dict[int, dict[str, int | None]] = {}
    for publication_id, items in by_pub.items():
        end_snap = items[-1]
        baseline = next((snap for snap in reversed(items) if snap.snapshot_date < analytics_range.start), None)
        values: dict[str, int | None] = {}
        for name in SOCIAL_METRICS:
            end_value = getattr(end_snap, name)
            base_value = getattr(baseline, name) if baseline else 0
            values[name] = None if end_value is None else max(0, int(end_value) - int(base_value or 0))
        out[publication_id] = values
    return out


def build_social_rows(
    db: Session,
    user_id: int,
    analytics_range: AnalyticsRange,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    publications = _successful_social_publications(db, user_id, analytics_range, platform)
    deltas = _social_metric_deltas(db, user_id, [row.id for row in publications], analytics_range)
    rows: list[dict[str, Any]] = []
    for publication in publications:
        values = deltas.get(publication.id, {})
        reactions = values.get("reactions") if values.get("reactions") is not None else values.get("likes")
        comments = values.get("comments")
        shares = values.get("shares") if values.get("shares") is not None else values.get("reposts")
        clicks = values.get("link_clicks")
        denominator = values.get("reach") if values.get("reach") is not None else values.get("impressions")
        available_parts = [v for v in (reactions, comments, shares, clicks) if v is not None]
        engagements = sum(available_parts) if available_parts else None
        engagement_rate = (engagements / denominator) if engagements is not None and denominator else None
        history = publication.history
        rows.append({
            "id": publication.id,
            "content": history.headline if history else "Untitled post",
            "platform": publication.platform,
            "related_blog_id": history.related_published_post_id if history else None,
            "related_blog_title": history.related_published_post.title if history and history.related_published_post else None,
            "publication_date": publication.published_at.isoformat() if publication.published_at else None,
            "status": "published",
            "impressions": _metric(values.get("impressions"), "pending" if not values else "unsupported"),
            "reach": _metric(values.get("reach"), "pending" if not values else "unsupported"),
            "likes": _metric(reactions, "pending" if not values else "unsupported"),
            "comments": _metric(comments, "pending" if not values else "unsupported"),
            "shares": _metric(shares, "pending" if not values else "unsupported"),
            "clicks": _metric(clicks, "pending" if not values else "unsupported"),
            "engagement_rate": _metric(round(engagement_rate, 4) if engagement_rate is not None else None, "pending" if not values else "unsupported"),
            "external_url": publication.external_url,
        })
    return rows


def _sum_available(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [row[field]["value"] for row in rows if row[field]["value"] is not None]
    return _metric(sum(values) if values else None, "not_connected" if not values else "available")


def _publishing_rates(db: Session, user_id: int, analytics_range: AnalyticsRange) -> dict[str, Any]:
    zone = _safe_zone(analytics_range.timezone_name)
    start_dt = datetime.combine(analytics_range.start, time.min, tzinfo=zone).astimezone(timezone.utc)
    end_dt = datetime.combine(analytics_range.end + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    blog_attempts = db.query(BlogPublicationAttempt).filter(
        BlogPublicationAttempt.user_id == user_id,
        BlogPublicationAttempt.requested_status == "publish",
        BlogPublicationAttempt.attempted_at >= start_dt,
        BlogPublicationAttempt.attempted_at < end_dt,
    ).all()
    social_attempts = db.query(SocialPublicationAttempt).filter(
        SocialPublicationAttempt.user_id == user_id,
        SocialPublicationAttempt.attempted_at >= start_dt,
        SocialPublicationAttempt.attempted_at < end_dt,
    ).all()

    def rate(rows: list[Any]) -> dict[str, Any]:
        successes = sum(1 for row in rows if row.outcome == "success")
        failures = sum(1 for row in rows if row.outcome == "failed")
        total = successes + failures
        return {"successes": successes, "failures": failures, "success_rate": round(successes / total, 4) if total else None}

    return {"blogs": rate(blog_attempts), "social": rate(social_attempts)}


def _follower_growth(db: Session, user_id: int, analytics_range: AnalyticsRange) -> dict[str, Any]:
    snapshots = db.query(SocialAccountMetricSnapshot).filter(
        SocialAccountMetricSnapshot.user_id == user_id,
        SocialAccountMetricSnapshot.snapshot_date <= analytics_range.end,
    ).order_by(
        SocialAccountMetricSnapshot.social_account_id,
        SocialAccountMetricSnapshot.snapshot_date,
    ).all()
    by_account: dict[int, list[SocialAccountMetricSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        by_account[snapshot.social_account_id].append(snapshot)

    current_total = 0
    growth_total = 0
    has_current = False
    comparable_accounts = 0
    for items in by_account.values():
        latest = items[-1]
        if latest.followers_count is None:
            continue
        has_current = True
        current_total += int(latest.followers_count)
        baseline = next(
            (item for item in reversed(items) if item.snapshot_date < analytics_range.start and item.followers_count is not None),
            None,
        )
        if baseline is not None:
            growth_total += int(latest.followers_count) - int(baseline.followers_count)
            comparable_accounts += 1
    return {
        "followers": _metric(current_total if has_current else None, "pending" if not snapshots else "unsupported"),
        "follower_growth": _metric(growth_total if comparable_accounts else None, "pending" if has_current else "unsupported"),
        "comparable_accounts": comparable_accounts,
    }


def _daily_series(
    db: Session,
    user_id: int,
    analytics_range: AnalyticsRange,
    website_id: int | None,
    article_urls: set[str],
) -> list[dict[str, Any]]:
    points = {
        analytics_range.start + timedelta(days=offset): {
            "date": (analytics_range.start + timedelta(days=offset)).isoformat(),
            "clicks": 0, "impressions": 0, "views": 0, "organic_traffic": 0,
        }
        for offset in range(analytics_range.days)
    }
    gsc_q = db.query(GscPageMetric).join(GscProperty).filter(
        GscProperty.user_id == user_id,
        GscPageMetric.date >= analytics_range.start,
        GscPageMetric.date <= analytics_range.end,
    )
    if website_id:
        gsc_q = gsc_q.filter(GscProperty.wordpress_site_id == website_id)
    normalized_article_urls = {normalize_url(url) for url in article_urls}
    for row in gsc_q.all():
        if normalize_url(row.page) not in normalized_article_urls:
            continue
        points[row.date]["clicks"] += row.clicks or 0
        points[row.date]["impressions"] += row.impressions or 0
    ga_q = db.query(GaPageMetric).join(GaProperty).filter(
        GaProperty.user_id == user_id,
        GaPageMetric.date >= analytics_range.start,
        GaPageMetric.date <= analytics_range.end,
    )
    if website_id:
        ga_q = ga_q.filter(GaProperty.wordpress_site_id == website_id)
    article_paths = {_path_only(url) for url in article_urls}
    for row in ga_q.all():
        if _path_only(row.page_path) not in article_paths:
            continue
        points[row.date]["views"] += row.views or 0
        points[row.date]["organic_traffic"] += row.organic_sessions or 0
    return list(points.values())


def build_overview(
    db: Session,
    user_id: int,
    analytics_range: AnalyticsRange,
    website_id: int | None = None,
    platform: str | None = None,
    category: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    blogs = build_blog_rows(db, user_id, analytics_range, website_id, category, author)
    social = build_social_rows(db, user_id, analytics_range, platform)
    now = datetime.now(_safe_zone(analytics_range.timezone_name)).date()
    published_dates = []
    for row in blogs:
        if not row.get("publication_date"):
            continue
        published_at = datetime.fromisoformat(row["publication_date"])
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        published_dates.append(published_at.astimezone(_safe_zone(analytics_range.timezone_name)).date())
    counts = {
        "total": len(blogs),
        "today": sum(1 for value in published_dates if value == now),
        "this_week": sum(1 for value in published_dates if value >= now - timedelta(days=now.weekday())),
        "this_month": sum(1 for value in published_dates if value.year == now.year and value.month == now.month),
    }
    platform_counts: dict[str, int] = defaultdict(int)
    for row in social:
        platform_counts[row["platform"]] += 1
    blog_impressions = _sum_available(blogs, "impressions")
    blog_clicks = _sum_available(blogs, "clicks")
    blog_views = _sum_available(blogs, "views")
    organic = _sum_available(blogs, "organic_traffic")
    total_impressions = int(blog_impressions["value"] or 0)
    total_clicks = int(blog_clicks["value"] or 0)
    weighted_positions = [
        (row["average_position"]["value"], row["impressions"]["value"])
        for row in blogs if row["average_position"]["value"] is not None and row["impressions"]["value"]
    ]
    avg_position = (
        sum(position * impressions for position, impressions in weighted_positions) / sum(impressions for _, impressions in weighted_positions)
        if weighted_positions else None
    )
    social_impressions = _sum_available(social, "impressions")
    social_reach = _sum_available(social, "reach")
    engagement_parts = {
        key: _sum_available(social, key) for key in ("likes", "comments", "shares", "clicks")
    }
    total_engagement = sum(int(value["value"] or 0) for value in engagement_parts.values())
    denominator = social_reach["value"] if social_reach["value"] is not None else social_impressions["value"]
    follower_metrics = _follower_growth(db, user_id, analytics_range)
    return {
        "range": {"start": analytics_range.start.isoformat(), "end": analytics_range.end.isoformat(), "timezone": analytics_range.timezone_name},
        "blog_summary": {
            "published": counts,
            "impressions": blog_impressions,
            "clicks": blog_clicks,
            "organic_traffic": organic,
            "views": blog_views,
            "ctr": _metric(round(total_clicks / total_impressions, 4) if total_impressions else 0.0),
            "average_position": _metric(round(avg_position, 2) if avg_position is not None else None, "pending" if avg_position is None else "available"),
            "views_per_article": _metric(round((blog_views["value"] or 0) / len(blogs), 1) if blogs and blog_views["value"] is not None else None, "not_connected" if blog_views["value"] is None else "available"),
        },
        "social_summary": {
            "published": len(social),
            "by_platform": dict(platform_counts),
            "impressions": social_impressions,
            "reach": social_reach,
            **engagement_parts,
            "engagement_rate": _metric(round(total_engagement / denominator, 4) if denominator else None, "pending" if denominator is None else "available"),
            **follower_metrics,
        },
        "publishing": _publishing_rates(db, user_id, analytics_range),
        "timeseries": _daily_series(
            db,
            user_id,
            analytics_range,
            website_id,
            {row["wordpress_url"] for row in blogs if row.get("wordpress_url")},
        ),
        "top_blogs": sorted(blogs, key=lambda row: ((row["views"]["value"] or -1), row["clicks"]["value"] or 0), reverse=True)[:5],
        "lowest_blogs": sorted(
            [row for row in blogs if row["views"]["value"] is not None],
            key=lambda row: (row["views"]["value"], row["clicks"]["value"] or 0),
        )[:5],
        "top_social": sorted(social, key=lambda row: row["engagement_rate"]["value"] or -1, reverse=True)[:5],
        "recent_blogs": blogs[:5],
        "recent_social": social[:5],
    }


def build_keyword_performance(
    db: Session,
    user_id: int,
    analytics_range: AnalyticsRange,
    website_id: int | None,
) -> list[dict[str, Any]]:
    blogs = build_blog_rows(db, user_id, analytics_range, website_id)
    by_url = {normalize_url(row["wordpress_url"]): row for row in blogs if row.get("wordpress_url")}
    if not by_url:
        return []
    query = db.query(GscQueryMetric).join(GscProperty).filter(
        GscProperty.user_id == user_id,
        GscQueryMetric.date >= analytics_range.start,
        GscQueryMetric.date <= analytics_range.end,
    )
    if website_id:
        query = query.filter(GscProperty.wordpress_site_id == website_id)
    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    for metric in query.all():
        blog = by_url.get(normalize_url(metric.page))
        if not blog:
            continue
        target = (blog.get("target_keyword") or "").lower()
        if target and metric.query.lower() != target and target not in metric.query.lower():
            continue
        key = (blog["id"], metric.query)
        agg = grouped.setdefault(key, {
            "blog_id": blog["id"], "article": blog["title"], "keyword": metric.query,
            "clicks": 0, "impressions": 0, "weighted_position": 0.0,
        })
        agg["clicks"] += metric.clicks or 0
        agg["impressions"] += metric.impressions or 0
        agg["weighted_position"] += (metric.position or 0.0) * (metric.impressions or 0)
    result = []
    for agg in grouped.values():
        impressions = agg.pop("impressions")
        weighted = agg.pop("weighted_position")
        result.append({
            **agg,
            "impressions": impressions,
            "ctr": round(agg["clicks"] / impressions, 4) if impressions else 0.0,
            "average_position": round(weighted / impressions, 2) if impressions else None,
        })
    return sorted(result, key=lambda row: row["impressions"], reverse=True)


def build_filters(db: Session, user_id: int) -> dict[str, Any]:
    websites = db.query(WordPressSite).filter(WordPressSite.user_id == user_id).order_by(WordPressSite.name).all()
    categories = [row[0] for row in db.query(PublishedPost.source_category).filter(
        PublishedPost.user_id == user_id, PublishedPost.source_category.isnot(None)
    ).distinct().order_by(PublishedPost.source_category).all()]
    authors = [row[0] for row in db.query(PublishedPost.wp_author_name).filter(
        PublishedPost.user_id == user_id, PublishedPost.wp_author_name.isnot(None)
    ).distinct().order_by(PublishedPost.wp_author_name).all()]
    platforms = [row[0] for row in db.query(SocialPostPublication.platform).filter(
        SocialPostPublication.user_id == user_id
    ).distinct().order_by(SocialPostPublication.platform).all()]
    return {
        "websites": [{"id": row.id, "name": row.name, "url": row.url} for row in websites],
        "categories": categories,
        "authors": authors,
        "platforms": platforms,
    }
