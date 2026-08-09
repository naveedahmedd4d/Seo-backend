"""Per-site keyword coverage: match trending keywords against WP published content."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.keyword_site_coverage import KeywordSiteCoverage
from app.models.trending_keywords import TrendingKeywords
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.services.cluster_keyword_service import SOURCE_CLUSTER
from app.services.manual_keyword_service import SOURCE_MANUAL, SOURCE_TRENDING
from app.services.wordpress_service import list_published_content

STATUS_EXISTING = "existing"
STATUS_NEW = "new"

MATCH_TITLE_EXACT = "title_exact"
MATCH_SLUG_EXACT = "slug_exact"
MATCH_TITLE_CONTAINS = "title_contains"
MATCH_SLUG_CONTAINS = "slug_contains"

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize_for_match(text: str) -> str:
    """Lower, strip, replace -/_ with space, drop punctuation, collapse whitespace."""
    if not text:
        return ""
    value = text.lower().strip()
    value = value.replace("-", " ").replace("_", " ")
    value = _PUNCT_RE.sub(" ", value)
    value = _SPACE_RE.sub(" ", value).strip()
    return value


def slug_from_url(url: str) -> str:
    """Last non-empty path segment, unquoted (not yet normalized)."""
    if not url:
        return ""
    path = urlparse(url.strip()).path or ""
    segments = [s for s in path.split("/") if s]
    if not segments:
        return ""
    return unquote(segments[-1])


def _phrase_contained(haystack: str, needle: str) -> bool:
    """True if needle is a whole-phrase substring of haystack (space-bounded)."""
    if not needle or not haystack:
        return False
    if needle == haystack:
        return True
    padded = f" {haystack} "
    return f" {needle} " in padded


def match_keyword(
    keyword: str,
    items: List[Dict[str, Any]],
) -> Optional[Tuple[Dict[str, Any], str]]:
    """
    Match keyword against published content items.
    First hit wins: title exact → slug exact → title contains → slug contains.
    Returns (item, match_method) or None.
    """
    needle = normalize_for_match(keyword)
    if not needle:
        return None

    prepared: List[Tuple[Dict[str, Any], str, str]] = []
    for item in items:
        title_n = normalize_for_match(str(item.get("title") or ""))
        slug_n = normalize_for_match(slug_from_url(str(item.get("url") or "")))
        prepared.append((item, title_n, slug_n))

    for item, title_n, _slug_n in prepared:
        if title_n and title_n == needle:
            return item, MATCH_TITLE_EXACT

    for item, _title_n, slug_n in prepared:
        if slug_n and slug_n == needle:
            return item, MATCH_SLUG_EXACT

    for item, title_n, _slug_n in prepared:
        if title_n and _phrase_contained(title_n, needle):
            return item, MATCH_TITLE_CONTAINS

    for item, _title_n, slug_n in prepared:
        if slug_n and _phrase_contained(slug_n, needle):
            return item, MATCH_SLUG_CONTAINS

    return None


def _load_keywords_for_source(db: Session, source: str) -> List[TrendingKeywords]:
    """Load keyword ORM rows for coverage sync (cluster includes mains + subs)."""
    if source == SOURCE_MANUAL:
        return (
            db.query(TrendingKeywords)
            .filter(TrendingKeywords.source == SOURCE_MANUAL)
            .order_by(TrendingKeywords.fetched_at.desc())
            .all()
        )
    if source == SOURCE_CLUSTER:
        return (
            db.query(TrendingKeywords)
            .filter(TrendingKeywords.source == SOURCE_CLUSTER)
            .order_by(TrendingKeywords.fetched_at.desc())
            .all()
        )
    if source == SOURCE_TRENDING:
        return (
            db.query(TrendingKeywords)
            .filter(TrendingKeywords.source == SOURCE_TRENDING)
            .order_by(TrendingKeywords.fetched_at.desc())
            .all()
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="source must be 'trending', 'manual', or 'cluster'",
    )


def get_site_for_user(db: Session, user: User, wordpress_site_id: int) -> WordPressSite:
    site = (
        db.query(WordPressSite)
        .filter(
            WordPressSite.id == wordpress_site_id,
            WordPressSite.user_id == user.id,
        )
        .first()
    )
    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress site not found",
        )
    return site


def coverage_map_for_site(
    db: Session,
    wordpress_site_id: int,
    keyword_ids: List[int],
) -> Dict[int, KeywordSiteCoverage]:
    if not keyword_ids:
        return {}
    rows = (
        db.query(KeywordSiteCoverage)
        .filter(
            KeywordSiteCoverage.wordpress_site_id == wordpress_site_id,
            KeywordSiteCoverage.trending_keyword_id.in_(keyword_ids),
        )
        .all()
    )
    return {row.trending_keyword_id: row for row in rows}


def coverage_to_dict(row: Optional[KeywordSiteCoverage]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "status": row.status,
        "matched_url": row.matched_url,
        "matched_title": row.matched_title,
        "match_method": row.match_method,
        "compared_at": row.compared_at.isoformat() if row.compared_at else None,
    }


async def sync_coverage_for_site(
    db: Session,
    user: User,
    wordpress_site_id: int,
    source: str,
) -> Dict[str, Any]:
    """
    Fetch WP published content and upsert coverage for all keywords of `source`.
    Returns {existing, new, compared_at, total}.
    """
    if source not in (SOURCE_TRENDING, SOURCE_MANUAL, SOURCE_CLUSTER):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source must be 'trending', 'manual', or 'cluster'",
        )

    site = get_site_for_user(db, user, wordpress_site_id)
    keywords = _load_keywords_for_source(db, source)
    items = await list_published_content(site, site.url)
    compared_at = datetime.now(timezone.utc)

    existing_count = 0
    new_count = 0

    existing_rows = {
        row.trending_keyword_id: row
        for row in db.query(KeywordSiteCoverage)
        .filter(
            KeywordSiteCoverage.wordpress_site_id == site.id,
            KeywordSiteCoverage.trending_keyword_id.in_([k.id for k in keywords] or [-1]),
        )
        .all()
    }

    for kw in keywords:
        hit = match_keyword(kw.keyword, items)
        if hit:
            item, method = hit
            status_val = STATUS_EXISTING
            matched_url = item.get("url")
            matched_title = item.get("title")
            match_method = method
            existing_count += 1
        else:
            status_val = STATUS_NEW
            matched_url = None
            matched_title = None
            match_method = None
            new_count += 1

        row = existing_rows.get(kw.id)
        if row is None:
            row = KeywordSiteCoverage(
                wordpress_site_id=site.id,
                trending_keyword_id=kw.id,
            )
            db.add(row)

        row.status = status_val
        row.matched_url = matched_url
        row.matched_title = matched_title
        row.match_method = match_method
        row.compared_at = compared_at

    db.commit()

    return {
        "existing": existing_count,
        "new": new_count,
        "total": len(keywords),
        "compared_at": compared_at.isoformat(),
        "wordpress_site_id": site.id,
        "source": source,
    }
