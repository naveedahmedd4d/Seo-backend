"""GitHub Trending: scrape github.com/trending (daily view only) and cache as keywords.

Only the default daily view is scraped. GitHub's robots.txt disallows any URL
containing a `since=` query parameter (`Disallow: /*since=*`), which is how the
weekly/monthly trending views are implemented, so those windows are intentionally
not supported here.
"""

import asyncio
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.models.trending_keywords import TrendingKeywords
from app.services.http_ssl import httpx_verify

SOURCE_GITHUB = "github"

# (query-param value, display label). "all" omits the language path segment.
GITHUB_LANGUAGES: List[tuple] = [
    ("all", "All"),
    ("python", "Python"),
    ("typescript", "TypeScript"),
    ("javascript", "JavaScript"),
    ("go", "Go"),
    ("rust", "Rust"),
    ("jupyter-notebook", "Jupyter Notebook"),
    ("other", "Other"),
]

_CURATED_LANGUAGE_LABELS = {
    "Python", "TypeScript", "JavaScript", "Go", "Rust", "Jupyter Notebook",
}

# Re-scrape at most once per hour per (language, user) — GitHub's trending list
# changes slowly, and this keeps us well clear of hammering the page.
FRESHNESS_WINDOW = timedelta(hours=1)

_last_request_time = 0.0
_min_request_interval = 2.0


async def _rate_limit():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _min_request_interval:
        await asyncio.sleep(_min_request_interval - elapsed)
    _last_request_time = time.time()


def _github_trending_url(language: str) -> str:
    if not language or language in ("all", "other"):
        return "https://github.com/trending"
    return f"https://github.com/trending/{language}"


def _parse_int_compact(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    cleaned = text.strip().replace(",", "")
    match = re.search(r"[\d.]+", cleaned)
    if not match:
        return None
    try:
        if "k" in cleaned.lower():
            return int(float(match.group()) * 1000)
        return int(float(match.group()))
    except ValueError:
        return None


def _parse_trending_html(html: str) -> List[Dict[str, Any]]:
    """
    Defensive scraper — GitHub redesigns this page periodically, so use fallback
    selectors and skip malformed rows rather than raising. Verify selectors
    against the live page before relying on this in production; the primary
    selector (`article.Box-row`) reflects GitHub's markup as of recent years.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("article.Box-row") or soup.select("article")
    results: List[Dict[str, Any]] = []

    for row in rows:
        try:
            link = row.select_one("h2 a") or row.select_one("h1 a") or row.find("a", href=True)
            if not link or not link.get("href"):
                continue
            full_name = link["href"].strip("/").strip()
            if "/" not in full_name:
                continue

            desc_el = row.select_one("p")
            description = desc_el.get_text(strip=True) if desc_el else ""

            lang_el = row.select_one('span[itemprop="programmingLanguage"]')
            language = lang_el.get_text(strip=True) if lang_el else None

            star_el = row.select_one('a[href$="/stargazers"]')
            stars = _parse_int_compact(star_el.get_text(strip=True)) if star_el else None

            fork_el = row.select_one('a[href$="/forks"]') or row.select_one('a[href*="/network/members"]')
            forks = _parse_int_compact(fork_el.get_text(strip=True)) if fork_el else None

            today_text = ""
            today_el = row.select_one("span.float-sm-right")
            if today_el:
                today_text = today_el.get_text(strip=True)
            if not today_text:
                found = row.find(string=re.compile(r"stars?\s+today", re.I))
                if found:
                    today_text = str(found)
            m = re.search(r"([\d,]+)\s+stars?\s+today", today_text, re.I)
            stars_today = _parse_int_compact(m.group(1)) if m else 0

            results.append({
                "full_name": full_name,
                "description": description,
                "language": language or "Other",
                "url": f"https://github.com/{full_name}",
                "stars": stars or 0,
                "forks": forks or 0,
                "stars_today": stars_today or 0,
                "topics": [],
            })
        except Exception:
            continue

    return results


async def _scrape_github_trending(language: str) -> List[Dict[str, Any]]:
    await _rate_limit()
    url = _github_trending_url(language)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SEOAutomationBot/1.0)"}
    async with httpx.AsyncClient(timeout=20.0, verify=httpx_verify(), headers=headers) as client:
        response = await client.get(url, follow_redirects=True)
        if response.status_code != 200:
            raise Exception(f"GitHub trending returned {response.status_code} for {url}")
        return _parse_trending_html(response.text)


def _normalize_language_category(raw_language: Optional[str]) -> str:
    if raw_language in _CURATED_LANGUAGE_LABELS:
        return raw_language
    return "Other"


def _row_to_dict(k: TrendingKeywords) -> Dict[str, Any]:
    data = k.data if isinstance(k.data, dict) else {}
    return {
        "id": k.id,
        "keyword": k.keyword,
        "data": data,
        "category": k.category,
        "fetched_at": k.fetched_at.isoformat() if k.fetched_at else None,
        "bio_blog_workflow_id": k.bio_blog_workflow_id,
        "net_worth_blog_workflow_id": k.net_worth_blog_workflow_id,
        "blog_workflow_id": k.blog_workflow_id,
        "source": k.source,
        "kd": k.kd,
        "value": data.get("value"),
        "timestamp": data.get("timestamp"),
    }


async def fetch_github_trending(
    db: Session,
    language: str,
    *,
    user_id: int,
) -> List[Dict[str, Any]]:
    """Scrape (if stale) + upsert into TrendingKeywords(source='github'), then return from DB."""
    cutoff = datetime.utcnow() - FRESHNESS_WINDOW
    recent_count = (
        db.query(TrendingKeywords)
        .filter(
            TrendingKeywords.source == SOURCE_GITHUB,
            TrendingKeywords.user_id == user_id,
            TrendingKeywords.fetched_at >= cutoff,
        )
        .count()
    )
    if recent_count > 0:
        return get_github_trending_from_db(db, language, user_id=user_id)

    repos = await _scrape_github_trending(language)
    if not repos:
        return get_github_trending_from_db(db, language, user_id=user_id)

    existing_by_name = {
        row.keyword: row
        for row in db.query(TrendingKeywords)
        .filter(
            TrendingKeywords.source == SOURCE_GITHUB,
            TrendingKeywords.user_id == user_id,
        )
        .all()
    }

    now = datetime.utcnow()
    for repo in repos:
        full_name = repo["full_name"]
        category = _normalize_language_category(repo["language"])
        data = {
            "description": repo["description"],
            "url": repo["url"],
            "language": repo["language"],
            "forks": repo["forks"],
            "topics": repo["topics"],
            "value": repo["stars"],
            "increase_percentage": repo["stars_today"],
            "timestamp": now.isoformat() + "Z",
        }
        row = existing_by_name.get(full_name)
        if row:
            existing_data = dict(row.data) if isinstance(row.data, dict) else {}
            for key in ("suggested_titles", "suggested_angles", "suggested_keywords"):
                if key in existing_data:
                    data[key] = existing_data[key]
            row.data = data
            row.category = category
            row.fetched_at = now
        else:
            db.add(TrendingKeywords(
                keyword=full_name,
                data=data,
                category=category,
                source=SOURCE_GITHUB,
                user_id=user_id,
                fetched_at=now,
            ))

    db.commit()
    return get_github_trending_from_db(db, language, user_id=user_id)


def get_github_trending_from_db(
    db: Session,
    language: str = "all",
    *,
    user_id: int,
) -> List[Dict[str, Any]]:
    query = db.query(TrendingKeywords).filter(
        TrendingKeywords.source == SOURCE_GITHUB,
        TrendingKeywords.user_id == user_id,
    )
    if language and language not in ("all",):
        label = dict(GITHUB_LANGUAGES).get(language, language)
        query = query.filter(TrendingKeywords.category == label)
    rows = query.order_by(TrendingKeywords.fetched_at.desc()).all()
    return [_row_to_dict(k) for k in rows]
