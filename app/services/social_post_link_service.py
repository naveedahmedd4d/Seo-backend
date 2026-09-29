"""Resolve first-party website links for social posts."""

from __future__ import annotations

import html
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.models.published_post import PublishedPost
from app.models.social_post_history import SocialPostHistory
from app.models.wordpress_site import WordPressSite
from app.services.http_ssl import httpx_verify

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_STOP_WORDS = {
    "about", "after", "again", "also", "and", "are", "article", "blog",
    "for", "from", "has", "have", "how", "into", "latest", "news", "not",
    "post", "that", "the", "their", "this", "today", "was", "were", "what",
    "when", "where", "which", "who", "why", "will", "with", "your",
}


def _plain_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("rendered") or ""
    return html.unescape(_HTML_TAG_RE.sub(" ", str(value or "")))


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _WORD_RE.findall(_plain_text(value).lower())
        if len(token) >= 3 and token not in _STOP_WORDS
    }


def relevance_score(topic: str, candidate: str) -> int:
    """Return a small deterministic relevance score for an article candidate."""
    topic_tokens = _tokens(topic)
    candidate_tokens = _tokens(candidate)
    overlap = topic_tokens & candidate_tokens
    if not overlap:
        return 0

    # One distinctive term is enough (for example, "OpenAI"); shorter/common
    # terms need a second match to avoid linking an unrelated article.
    if len(overlap) == 1 and len(next(iter(overlap))) < 6:
        return 0
    return sum(min(len(token), 12) for token in overlap) + (5 * len(overlap))


def _history_topic(history: SocialPostHistory) -> str:
    return " ".join(
        str(value or "")
        for value in (
            history.query,
            history.headline,
            history.description,
            history.story_description,
        )
    )


def _published_post_text(post: PublishedPost) -> str:
    return " ".join(
        str(value or "")
        for value in (post.title, post.target_keyword, post.source_category)
    )


def _published_post_url(post: PublishedPost) -> str | None:
    site = post.wordpress_site
    site_host = (urlparse(site.url).hostname or "").lower().removeprefix("www.")
    for raw in (post.canonical_url, post.wp_link):
        value = str(raw or "").strip()
        value_host = (urlparse(value).hostname or "").lower().removeprefix("www.")
        if value and site_host and value_host == site_host:
            return value
    return None


async def _search_live_wordpress(site: WordPressSite, topic: str) -> tuple[int, str] | None:
    search = " ".join(str(topic or "").split()).strip()
    if not search:
        return None

    try:
        async with httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=True,
            verify=httpx_verify(),
        ) as client:
            response = await client.get(
                f"{site.url.rstrip('/')}/wp-json/wp/v2/posts",
                params={
                    "search": search[:200],
                    "per_page": 10,
                    "status": "publish",
                    "_fields": "link,title,slug",
                },
                headers={"User-Agent": "SEO-Automation/1.0"},
            )
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:  # A site lookup must never prevent publishing.
        logger.warning("Could not search WordPress site %s for Facebook link: %s", site.id, exc)
        return None

    best: tuple[int, str] | None = None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        link = str(row.get("link") or "").strip()
        if not link:
            continue
        site_host = (urlparse(site.url).hostname or "").lower().removeprefix("www.")
        link_host = (urlparse(link).hostname or "").lower().removeprefix("www.")
        if not site_host or link_host != site_host:
            continue
        candidate = f"{_plain_text(row.get('title'))} {row.get('slug') or ''}"
        score = relevance_score(topic, candidate)
        if score and (best is None or score > best[0]):
            best = (score, link)
    return best


async def resolve_facebook_destination_url(
    db: Session,
    user_id: int,
    history: SocialPostHistory,
) -> str | None:
    """Choose a first-party URL for a Facebook photo caption.

    Priority: explicitly related published article, relevant tracked article,
    relevant live WordPress article, then the primary connected site's homepage.
    External research/news URLs are intentionally never returned.
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

    topic = _history_topic(history)
    tracked = (
        db.query(PublishedPost)
        .join(WordPressSite, WordPressSite.id == PublishedPost.wordpress_site_id)
        .filter(
            PublishedPost.user_id == user_id,
            WordPressSite.user_id == user_id,
            PublishedPost.wp_status == "publish",
        )
        .order_by(PublishedPost.published_at.desc())
        .all()
    )
    best_tracked: tuple[int, str] | None = None
    for post in tracked:
        link = _published_post_url(post)
        if not link:
            continue
        score = relevance_score(topic, _published_post_text(post))
        if score and (best_tracked is None or score > best_tracked[0]):
            best_tracked = (score, link)
    if best_tracked:
        return best_tracked[1]

    live_topic = str(history.query or history.headline or "").strip()
    best_live: tuple[int, str] | None = None
    for site in sites:
        candidate = await _search_live_wordpress(site, live_topic)
        if candidate and (best_live is None or candidate[0] > best_live[0]):
            best_live = candidate
    if best_live:
        return best_live[1]

    return sites[0].url.rstrip("/") or None
