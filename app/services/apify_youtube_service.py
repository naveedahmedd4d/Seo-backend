"""
Scrape YouTube videos for a celebrity's interviews using Apify YouTube Scraper.
Only returns videos where title or description clearly indicates an interview.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Apify YouTube Scraper (streamers/youtube-scraper) - supports search by searchQueries
APIFY_YOUTUBE_ACTOR_ID = "streamers~youtube-scraper"
APIFY_BASE = "https://api.apify.com/v2"
# Max results per search query to keep runs fast and cheap
MAX_VIDEOS_PER_QUERY = 25
# Poll interval and timeout for run completion
POLL_INTERVAL_SEC = 5
RUN_TIMEOUT_SEC = 300

# Phrases that clearly indicate an interview (title or description)
INTERVIEW_PHRASES = (
    "interview",
    "exclusive interview",
    "full interview",
    "in conversation",
    "in conversation with",
    "talks to",
    "speaks to",
    "q&a",
    "q and a",
    "question and answer",
)


def _is_interview_video(item: Dict[str, Any]) -> bool:
    """Return True only if title or description clearly indicates an interview."""
    title = (item.get("title") or "").lower().strip()
    description = (item.get("text") or item.get("description") or "").lower().strip()
    combined = f"{title} {description}"
    return any(phrase in combined for phrase in INTERVIEW_PHRASES)


def _normalize_video(item: Dict[str, Any]) -> Dict[str, Any]:
    """Extract a stable record for a video (url, title, description, id, etc.)."""
    url = (item.get("url") or "").strip()
    if not url and item.get("id"):
        url = f"https://www.youtube.com/watch?v={item['id']}"
    return {
        "id": item.get("id"),
        "url": url or None,
        "title": (item.get("title") or "").strip() or None,
        "description": (item.get("text") or item.get("description") or "").strip() or None,
        "channelName": (item.get("channelName") or "").strip() or None,
        "viewCount": item.get("viewCount"),
        "date": item.get("date"),
        "duration": item.get("duration"),
    }


async def _run_actor_and_wait(
    client: httpx.AsyncClient,
    token: str,
    run_input: Dict[str, Any],
) -> str:
    """Start actor run and wait until finished; return defaultDatasetId."""
    run_url = f"{APIFY_BASE}/acts/{APIFY_YOUTUBE_ACTOR_ID}/runs"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = await client.post(run_url, json=run_input, headers=headers)
    r.raise_for_status()
    data = r.json()
    run_id = data.get("data", {}).get("id")
    dataset_id = data.get("data", {}).get("defaultDatasetId")
    if not run_id or not dataset_id:
        raise ValueError("Apify run response missing id or defaultDatasetId")
    elapsed = 0
    while elapsed < RUN_TIMEOUT_SEC:
        status_url = f"{APIFY_BASE}/actor-runs/{run_id}"
        sr = await client.get(status_url, headers={"Authorization": f"Bearer {token}"})
        sr.raise_for_status()
        status_data = sr.json()
        status = (status_data.get("data", {}).get("status") or "").lower()
        if status == "succeeded":
            return dataset_id
        if status in ("failed", "aborted", "timed-out"):
            raise RuntimeError(f"Apify run ended with status: {status}")
        await asyncio.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC
    raise TimeoutError("Apify YouTube scraper run timed out")


async def _get_dataset_items(
    client: httpx.AsyncClient,
    token: str,
    dataset_id: str,
) -> List[Dict[str, Any]]:
    """Fetch all items from an Apify dataset."""
    url = f"{APIFY_BASE}/datasets/{dataset_id}/items"
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.get(url, headers=headers)
    r.raise_for_status()
    return r.json()


async def scrape_youtube_interviews(celebrity_name: str) -> List[Dict[str, Any]]:
    """
    Scrape YouTube for videos that are clearly interviews of the given celebrity.

    Uses search queries:
      - {celebrity_name} interview
      - {celebrity_name} exclusive interview
      - {celebrity_name} full interview

    Only includes videos where the title or description clearly indicates an
    interview (e.g. "interview", "exclusive interview", "full interview").

    Returns a list of dicts with keys: id, url, title, description, channelName,
    viewCount, date, duration. Duplicates (by video id) are removed.
    """
    if not celebrity_name or not (celebrity_name := celebrity_name.strip()):
        return []
    token = settings.apify_api_key
    if not token:
        logger.warning("APIFY_API_KEY not set; cannot scrape YouTube interviews")
        return []

    search_queries = [
        f"{celebrity_name} interview",
        f"{celebrity_name} exclusive interview",
        f"{celebrity_name} full interview",
    ]
    run_input = {
        "searchQueries": search_queries,
        "maxResults": MAX_VIDEOS_PER_QUERY,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            dataset_id = await _run_actor_and_wait(client, token, run_input)
            items = await _get_dataset_items(client, token, dataset_id)
    except Exception as e:
        logger.exception("Apify YouTube scrape failed: %s", e)
        return []

    seen_ids: set = set()
    results: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        vid = item.get("id")
        if vid and vid in seen_ids:
            continue
        if not _is_interview_video(item):
            continue
        seen_ids.add(vid)
        results.append(_normalize_video(item))

    # Sort by viewCount descending when available
    results.sort(key=lambda x: (x.get("viewCount") is None, -(x.get("viewCount") or 0)))
    return results


async def get_one_youtube_interview_url(celebrity_name: str) -> Optional[str]:
    """
    Return a single YouTube interview URL for the celebrity, or None.
    Uses Apify when APIFY_API_KEY is set; otherwise returns None (caller can
    fall back to Tavily find_youtube_interview).
    """
    videos = await scrape_youtube_interviews(celebrity_name)
    for v in videos:
        url = v.get("url")
        if url:
            return url
    return None
