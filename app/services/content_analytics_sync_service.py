from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.content_analytics import (
    AnalyticsSyncRun,
    SocialAccountMetricSnapshot,
    SocialPostMetricSnapshot,
)
from app.models.published_post import PublishedPost
from app.models.social_account import SocialAccount
from app.models.social_post_publication import SocialPostPublication
from app.models.wordpress_site import WordPressSite
from app.services.bluesky_service import get_valid_bluesky_session
from app.services.facebook_service import get_valid_facebook_page_context
from app.services.http_ssl import httpx_verify
from app.services.instagram_service import get_valid_instagram_context
from app.services.linkedin_service import LINKEDIN_API_BASE, get_valid_linkedin_access_token
from app.services.mastodon_service import get_valid_mastodon_access_token
from app.services.meta_graph_service import graph_get
from app.services.social_account_service import get_account_metadata
from app.services.wordpress_service import decrypt_api_key
from app.services.x_service import X_API_BASE, get_valid_x_access_token

logger = logging.getLogger(__name__)


def _availability(state: str, detail: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"default": state}
    if detail:
        payload["detail"] = detail[:500]
    return payload


def _upsert_post_snapshot(
    db: Session,
    publication: SocialPostPublication,
    metrics: dict[str, int | None],
    availability: dict[str, Any],
) -> None:
    today = date.today()
    row = db.query(SocialPostMetricSnapshot).filter(
        SocialPostMetricSnapshot.publication_id == publication.id,
        SocialPostMetricSnapshot.snapshot_date == today,
    ).first()
    if not row:
        row = SocialPostMetricSnapshot(
            user_id=publication.user_id,
            publication_id=publication.id,
            social_account_id=publication.social_account_id,
            snapshot_date=today,
        )
        db.add(row)
    for name in ("impressions", "reach", "likes", "reactions", "comments", "shares", "reposts", "link_clicks"):
        setattr(row, name, metrics.get(name))
    row.availability = availability
    row.raw_metrics = metrics
    row.captured_at = datetime.now(timezone.utc)


def _upsert_follower_snapshot(
    db: Session,
    account: SocialAccount,
    followers: int | None,
    availability: dict[str, Any],
) -> None:
    today = date.today()
    row = db.query(SocialAccountMetricSnapshot).filter(
        SocialAccountMetricSnapshot.social_account_id == account.id,
        SocialAccountMetricSnapshot.snapshot_date == today,
    ).first()
    if not row:
        row = SocialAccountMetricSnapshot(
            user_id=account.user_id,
            social_account_id=account.id,
            snapshot_date=today,
        )
        db.add(row)
    row.followers_count = followers
    row.availability = availability
    row.captured_at = datetime.now(timezone.utc)


async def sync_wordpress_publications(db: Session, user_id: int) -> dict[str, int]:
    rows = (
        db.query(PublishedPost)
        .join(WordPressSite, WordPressSite.id == PublishedPost.wordpress_site_id)
        .filter(PublishedPost.user_id == user_id, WordPressSite.user_id == user_id)
        .all()
    )
    verified = 0
    unavailable = 0
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        for row in rows:
            site = row.wordpress_site
            try:
                response = await client.get(
                    f"{site.url.rstrip('/')}/wp-json/wp/v2/posts/{row.wp_post_id}",
                    params={"context": "edit", "_fields": "id,status,link,date_gmt,author,categories"},
                    auth=(site.username, decrypt_api_key(site.api_key)),
                )
                response.raise_for_status()
                payload = response.json()
                row.remote_status = payload.get("status")
                row.wp_status = payload.get("status") or row.wp_status
                row.wp_link = payload.get("link") or row.wp_link
                row.canonical_url = payload.get("link") or row.canonical_url or row.wp_link
                row.wp_author_id = payload.get("author")
                date_gmt = payload.get("date_gmt")
                if date_gmt:
                    row.wordpress_published_at = datetime.fromisoformat(str(date_gmt).replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
                if row.wp_author_id:
                    author_response = await client.get(
                        f"{site.url.rstrip('/')}/wp-json/wp/v2/users/{row.wp_author_id}",
                        params={"_fields": "name"},
                        auth=(site.username, decrypt_api_key(site.api_key)),
                    )
                    if author_response.status_code == 200:
                        row.wp_author_name = author_response.json().get("name")
                category_ids = payload.get("categories") or []
                if category_ids and not row.source_category:
                    category_response = await client.get(
                        f"{site.url.rstrip('/')}/wp-json/wp/v2/categories",
                        params={"include": ",".join(str(item) for item in category_ids), "per_page": 100, "_fields": "name"},
                        auth=(site.username, decrypt_api_key(site.api_key)),
                    )
                    if category_response.status_code == 200:
                        names = [
                            str(item.get("name")).strip()
                            for item in category_response.json()
                            if isinstance(item, dict) and item.get("name")
                        ]
                        if names:
                            row.source_category = ", ".join(names)
                row.last_verified_at = datetime.now(timezone.utc)
                verified += 1
            except Exception as exc:
                logger.warning("WordPress analytics verification failed for post %s: %s", row.id, exc)
                unavailable += 1
    db.commit()
    return {"verified": verified, "unavailable": unavailable}


async def _x_metrics(db: Session, publication: SocialPostPublication) -> tuple[dict[str, int | None], dict[str, Any]]:
    token = await get_valid_x_access_token(db, publication.user_id)
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{X_API_BASE}/tweets/{publication.external_id}",
            params={"tweet.fields": "public_metrics,organic_metrics,non_public_metrics"},
            headers={"Authorization": f"Bearer {token}"},
        )
    response.raise_for_status()
    data = response.json().get("data") or {}
    public = data.get("public_metrics") or {}
    organic = data.get("organic_metrics") or {}
    non_public = data.get("non_public_metrics") or {}
    metrics = {
        "impressions": organic.get("impression_count") or non_public.get("impression_count"),
        "reach": None,
        "likes": public.get("like_count"),
        "reactions": public.get("like_count"),
        "comments": public.get("reply_count"),
        "shares": public.get("retweet_count"),
        "reposts": (public.get("retweet_count") or 0) + (public.get("quote_count") or 0),
        "link_clicks": organic.get("url_link_clicks") or non_public.get("url_link_clicks"),
    }
    return metrics, _availability("available")


async def _mastodon_metrics(db: Session, publication: SocialPostPublication) -> tuple[dict[str, int | None], dict[str, Any]]:
    token, instance_url = await get_valid_mastodon_access_token(db, publication.user_id)
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{instance_url.rstrip('/')}/api/v1/statuses/{publication.external_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    response.raise_for_status()
    data = response.json()
    metrics = {
        "impressions": None, "reach": None,
        "likes": data.get("favourites_count"), "reactions": data.get("favourites_count"),
        "comments": data.get("replies_count"), "shares": data.get("reblogs_count"),
        "reposts": data.get("reblogs_count"), "link_clicks": None,
    }
    return metrics, _availability("partial")


async def _bluesky_metrics(publication: SocialPostPublication) -> tuple[dict[str, int | None], dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread",
            params={"uri": publication.external_id, "depth": 0},
        )
    response.raise_for_status()
    post = ((response.json().get("thread") or {}).get("post") or {})
    metrics = {
        "impressions": None, "reach": None,
        "likes": post.get("likeCount"), "reactions": post.get("likeCount"),
        "comments": post.get("replyCount"), "shares": post.get("repostCount"),
        "reposts": post.get("repostCount"), "link_clicks": None,
    }
    return metrics, _availability("partial")


async def _facebook_metrics(db: Session, publication: SocialPostPublication) -> tuple[dict[str, int | None], dict[str, Any]]:
    _, _, page_token = await get_valid_facebook_page_context(db, publication.user_id)
    payload = await graph_get(
        publication.external_id,
        page_token,
        params={
            "fields": (
                "likes.limit(0).summary(true),comments.limit(0).summary(true),shares,"
                "insights.metric(post_impressions,post_impressions_unique,post_clicks)"
            )
        },
    )
    insights = {
        item.get("name"): ((item.get("values") or [{}])[0].get("value"))
        for item in ((payload.get("insights") or {}).get("data") or [])
    }
    metrics = {
        "impressions": insights.get("post_impressions"),
        "reach": insights.get("post_impressions_unique"),
        "likes": ((payload.get("likes") or {}).get("summary") or {}).get("total_count"),
        "reactions": ((payload.get("likes") or {}).get("summary") or {}).get("total_count"),
        "comments": ((payload.get("comments") or {}).get("summary") or {}).get("total_count"),
        "shares": (payload.get("shares") or {}).get("count"),
        "reposts": (payload.get("shares") or {}).get("count"),
        "link_clicks": insights.get("post_clicks"),
    }
    return metrics, _availability("available")


async def _instagram_metrics(db: Session, publication: SocialPostPublication) -> tuple[dict[str, int | None], dict[str, Any]]:
    _, _, page_token = await get_valid_instagram_context(db, publication.user_id)
    post = await graph_get(
        publication.external_id,
        page_token,
        params={"fields": "like_count,comments_count,permalink"},
    )
    insights = await graph_get(
        f"{publication.external_id}/insights",
        page_token,
        params={"metric": "reach,views,shares,total_interactions"},
    )
    values = {
        item.get("name"): ((item.get("values") or [{}])[0].get("value"))
        for item in insights.get("data") or []
    }
    metrics = {
        "impressions": values.get("views"),
        "reach": values.get("reach"),
        "likes": post.get("like_count"),
        "reactions": post.get("like_count"),
        "comments": post.get("comments_count"),
        "shares": values.get("shares"),
        "reposts": values.get("shares"),
        "link_clicks": None,
    }
    return metrics, _availability("partial", "Instagram does not expose per-post link clicks")


async def _linkedin_metrics(db: Session, publication: SocialPostPublication) -> tuple[dict[str, int | None], dict[str, Any]]:
    token, _ = await get_valid_linkedin_access_token(db, publication.user_id)
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{LINKEDIN_API_BASE}/v2/socialActions/{publication.external_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
    response.raise_for_status()
    payload = response.json()
    metrics = {
        "impressions": None,
        "reach": None,
        "likes": (payload.get("likesSummary") or {}).get("totalLikes"),
        "reactions": (payload.get("likesSummary") or {}).get("totalLikes"),
        "comments": (payload.get("commentsSummary") or {}).get("totalFirstLevelComments"),
        "shares": None,
        "reposts": None,
        "link_clicks": None,
    }
    return metrics, _availability("partial", "LinkedIn member analytics access is limited by the connected app permissions")


async def sync_social_publications(db: Session, user_id: int) -> dict[str, int]:
    rows = db.query(SocialPostPublication).filter(
        SocialPostPublication.user_id == user_id,
        SocialPostPublication.status == "success",
        SocialPostPublication.external_id.isnot(None),
    ).all()
    synced = 0
    unavailable = 0
    for row in rows:
        try:
            if row.platform == "x":
                metrics, availability = await _x_metrics(db, row)
            elif row.platform == "mastodon":
                metrics, availability = await _mastodon_metrics(db, row)
            elif row.platform == "bluesky":
                metrics, availability = await _bluesky_metrics(row)
            elif row.platform == "facebook":
                metrics, availability = await _facebook_metrics(db, row)
            elif row.platform == "instagram":
                metrics, availability = await _instagram_metrics(db, row)
            elif row.platform == "linkedin":
                metrics, availability = await _linkedin_metrics(db, row)
            elif row.platform == "pinterest":
                metrics = {name: None for name in ("impressions", "reach", "likes", "reactions", "comments", "shares", "reposts", "link_clicks")}
                availability = _availability("permission_required", "Reconnect the account with analytics permissions")
                unavailable += 1
            else:
                metrics = {name: None for name in ("impressions", "reach", "likes", "reactions", "comments", "shares", "reposts", "link_clicks")}
                availability = _availability("unsupported")
                unavailable += 1
            _upsert_post_snapshot(db, row, metrics, availability)
            row.last_verified_at = datetime.now(timezone.utc)
            row.remote_status = "published"
            synced += 1
        except Exception as exc:
            logger.warning("Social analytics sync failed for publication %s: %s", row.id, exc)
            _upsert_post_snapshot(
                db, row,
                {name: None for name in ("impressions", "reach", "likes", "reactions", "comments", "shares", "reposts", "link_clicks")},
                _availability("error", str(exc)),
            )
            unavailable += 1
    db.commit()
    return {"synced": synced, "unavailable": unavailable}


async def sync_follower_counts(db: Session, user_id: int) -> dict[str, int]:
    accounts = db.query(SocialAccount).filter(SocialAccount.user_id == user_id).all()
    synced = 0
    for account in accounts:
        followers: int | None = None
        availability = _availability("unsupported")
        try:
            if account.platform == "x":
                token = await get_valid_x_access_token(db, user_id)
                async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
                    response = await client.get(
                        f"{X_API_BASE}/users/me",
                        params={"user.fields": "public_metrics"},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                response.raise_for_status()
                followers = ((response.json().get("data") or {}).get("public_metrics") or {}).get("followers_count")
                availability = _availability("available")
            elif account.platform == "mastodon":
                token, instance_url = await get_valid_mastodon_access_token(db, user_id)
                async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
                    response = await client.get(
                        f"{instance_url.rstrip('/')}/api/v1/accounts/verify_credentials",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                response.raise_for_status()
                followers = response.json().get("followers_count")
                availability = _availability("available")
            elif account.platform == "bluesky":
                _, did = await get_valid_bluesky_session(db, user_id)
                actor = get_account_metadata(account).get("handle") or did
                async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
                    response = await client.get(
                        "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
                        params={"actor": actor},
                    )
                response.raise_for_status()
                followers = response.json().get("followersCount")
                availability = _availability("available")
            elif account.platform == "facebook":
                page_id, _, page_token = await get_valid_facebook_page_context(db, user_id)
                payload = await graph_get(page_id, page_token, params={"fields": "followers_count"})
                followers = payload.get("followers_count")
                availability = _availability("available")
            elif account.platform == "instagram":
                instagram_id, _, page_token = await get_valid_instagram_context(db, user_id)
                payload = await graph_get(instagram_id, page_token, params={"fields": "followers_count"})
                followers = payload.get("followers_count")
                availability = _availability("available")
        except Exception as exc:
            availability = _availability("error", str(exc))
        _upsert_follower_snapshot(db, account, followers, availability)
        synced += 1
    db.commit()
    return {"synced": synced}


async def execute_sync_run(run_id: int, user_id: int) -> None:
    db = SessionLocal()
    run = None
    try:
        run = db.query(AnalyticsSyncRun).filter(
            AnalyticsSyncRun.id == run_id,
            AnalyticsSyncRun.user_id == user_id,
        ).first()
        if not run:
            return
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.commit()
        result: dict[str, Any] = {}
        if run.scope in {"all", "wordpress"}:
            result["wordpress"] = await sync_wordpress_publications(db, user_id)
        if run.scope in {"all", "gsc"}:
            from app.services.gsc_sync_service import sync_all_properties
            result["gsc"] = await sync_all_properties(db, user_id)
        if run.scope in {"all", "ga"}:
            try:
                from app.services.ga_analytics_service import sync_all_ga_properties
                result["ga"] = await sync_all_ga_properties(db, user_id)
            except Exception as exc:
                result["ga"] = {"error": str(exc)}
        if run.scope in {"all", "social"}:
            result["social"] = await sync_social_publications(db, user_id)
            result["followers"] = await sync_follower_counts(db, user_id)
        run.result = result
        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        logger.exception("Content analytics sync run %s failed", run_id)
        db.rollback()
        if run:
            run = db.query(AnalyticsSyncRun).filter(AnalyticsSyncRun.id == run_id).first()
            if run:
                run.status = "failed"
                run.error_message = str(exc)[:2000]
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
    finally:
        db.close()
