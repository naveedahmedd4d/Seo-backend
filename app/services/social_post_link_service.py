"""Resolve first-party website links for social posts."""

from __future__ import annotations

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


def _first_party_news_link(history: SocialPostHistory, site_hosts: set[str]) -> str | None:
    """Return a news_results link that already points at the user's WordPress site.

    Used by weekly blog-promotion flows that stash the WordPress permalink in
    news_results without setting related_published_post_id. External research
    URLs are ignored so standalone social posts stay link-free.
    """
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

    Blog posts (explicit related article, or a first-party WordPress link already
    stored on the history row) get that URL. Standalone social posts get None —
    no fuzzy article match and no homepage fallback — so Facebook captions are
    not forced to include a blog link.
    """
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
