"""Resolve first-party website links for social posts."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.published_post import PublishedPost
from app.models.social_post_history import SocialPostHistory
from app.models.wordpress_site import WordPressSite


def _published_post_url(post: PublishedPost) -> str | None:
    site = post.wordpress_site
    site_host = (urlparse(site.url).hostname or "").lower().removeprefix("www.")
    for raw in (post.canonical_url, post.wp_link):
        value = str(raw or "").strip()
        value_host = (urlparse(value).hostname or "").lower().removeprefix("www.")
        if value and site_host and value_host == site_host:
            return value
    return None


def _site_hosts(sites: list[WordPressSite]) -> set[str]:
    hosts: set[str] = set()
    for site in sites:
        host = (urlparse(site.url).hostname or "").lower().removeprefix("www.")
        if host:
            hosts.add(host)
    return hosts


def _settings_snapshot(history: SocialPostHistory) -> dict[str, Any]:
    snap = history.settings_snapshot
    return snap if isinstance(snap, dict) else {}


def is_blog_promotion_post(history: SocialPostHistory) -> bool:
    """True only when the post is meant to promote a blog (include website URL).

    Standalone Social Posts must stay link-free even if news_results happen to
    contain a first-party WordPress URL.
    """
    if history.related_published_post_id:
        return True
    snap = _settings_snapshot(history)
    if snap.get("match_blog") is True:
        return True
    if snap.get("post_type") == "blog":
        return True
    return False


def _first_party_news_link(history: SocialPostHistory, site_hosts: set[str]) -> str | None:
    """Return a news_results link that already points at the user's WordPress site."""
    if not site_hosts:
        return None
    rows = history.news_results if isinstance(history.news_results, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        link = str(row.get("link") or "").strip()
        if not link:
            continue
        link_host = (urlparse(link).hostname or "").lower().removeprefix("www.")
        if link_host and link_host in site_hosts:
            return link
    return None


async def resolve_facebook_destination_url(
    db: Session,
    user_id: int,
    history: SocialPostHistory,
) -> str | None:
    """Choose a first-party URL for a Facebook photo caption.

    Social Posts return None (no website URL in the caption). Blog Posts return
    the related article URL, or a first-party WordPress permalink already stored
    on the history row for weekly blog-promotion flows.
    """
    if not is_blog_promotion_post(history):
        return None

    sites = (
        db.query(WordPressSite)
        .filter(WordPressSite.user_id == user_id)
        .order_by(WordPressSite.created_at.asc(), WordPressSite.id.asc())
        .all()
    )
    if not sites:
        return None

    if history.related_published_post_id:
        explicit = (
            db.query(PublishedPost)
            .join(WordPressSite, WordPressSite.id == PublishedPost.wordpress_site_id)
            .filter(
                PublishedPost.id == history.related_published_post_id,
                PublishedPost.user_id == user_id,
                WordPressSite.user_id == user_id,
                PublishedPost.wp_status == "publish",
            )
            .first()
        )
        if explicit:
            explicit_url = _published_post_url(explicit)
            if explicit_url:
                return explicit_url

    return _first_party_news_link(history, _site_hosts(sites))
