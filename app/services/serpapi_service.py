import httpx
import asyncio
import re
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.trending_keywords import TrendingKeywords
from app.config import settings
from app.services.http_ssl import httpx_verify
import time

# Groq model for trending fallback
TRENDING_FALLBACK_GROQ_MODEL = "llama-3.3-70b-versatile"

# Simple rate limiting
_last_request_time = 0
_min_request_interval = 1.0  # Minimum seconds between requests


async def _rate_limit():
    """Simple rate limiting"""
    global _last_request_time
    current_time = time.time()
    time_since_last = current_time - _last_request_time
    if time_since_last < _min_request_interval:
        await asyncio.sleep(_min_request_interval - time_since_last)
    _last_request_time = time.time()


def _get_groq_fallback_key(override: Optional[str] = None) -> Optional[str]:
    """Groq API key for trending fallback (caller-resolved or legacy env)."""
    if override and override.strip():
        return override.strip()
    return settings.trending_fallback_groq_api_key or settings.groq_api_key


def _is_serpapi_rate_limit(status_code: int, error_message: str) -> bool:
    """True if SerpAPI response indicates 429 or rate limit / out of searches."""
    if status_code == 429:
        return True
    msg = (error_message or "").lower()
    return (
        "rate limit" in msg
        or "run out of searches" in msg
        or "too many requests" in msg
        or "quota" in msg
    )


async def _fetch_trending_keywords_via_groq(
    db: Session,
    hours: int,
    *,
    groq_api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fallback: generate trending-style keywords via Groq when SerpAPI is rate limited."""
    api_key = _get_groq_fallback_key(groq_api_key)
    if not api_key:
        raise ValueError(
            "SerpAPI rate limit hit and Groq fallback not configured. "
            "Add a Trending fallback Groq or Groq API key in Settings."
        )
    await _rate_limit()
    prompt = f"""List 20 currently trending search keywords or topics (last {hours} hours). 
Return only the keywords, one per line, no numbers or bullets. 
Examples: AI news, weather today, election results, sports scores."""
    try:
        async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": TRENDING_FALLBACK_GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
            if response.status_code != 200:
                err = response.text
                try:
                    j = response.json()
                    err = j.get("error", {}).get("message", err)
                except Exception:
                    pass
                raise Exception(f"Groq API error: {err}")
            data = response.json()
            content = (
                (data.get("choices") or [{}])[0]
                .get("message", {})
                .get("content", "")
            )
    except Exception as e:
        raise Exception(f"Trending fallback (Groq) failed: {str(e)}")
    # Parse lines: non-empty, strip numbering/bullets
    keywords = []
    for line in content.splitlines():
        line = re.sub(r"^[\d\.\)\-\*]+\s*", "", line.strip()).strip()
        if line and len(line) < 200:
            keywords.append({
                "keyword": line,
                "value": "—",
                "timestamp": datetime.utcnow().isoformat(),
            })
    if not keywords:
        raise Exception("Groq fallback returned no parseable trending keywords")
    cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    for keyword_data in keywords[:25]:
        keyword = keyword_data.get("keyword")
        if not keyword:
            continue
        existing = db.query(TrendingKeywords).filter(
            TrendingKeywords.keyword == keyword,
            TrendingKeywords.fetched_at >= cutoff_time,
            TrendingKeywords.source == "trending",
        ).first()
        if not existing:
            db.add(TrendingKeywords(keyword=keyword, data=keyword_data, source="trending"))
    db.commit()
    return get_trending_keywords_from_db(db, hours)


async def fetch_trending_keywords(
    db: Session,
    hours: int = 4,
    *,
    api_key: Optional[str] = None,
    groq_fallback_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch trending keywords from SerpAPI (last N hours)."""
    serp_key = (api_key or settings.serpapi_key or "").strip()
    if not serp_key:
        raise ValueError("Add your SerpAPI API key in Settings")
    
    await _rate_limit()
    
    try:
        # SerpAPI Google Trends Trending Now endpoint
        # This engine is specifically designed for trending searches and doesn't require 'q' or 'category'
        # It supports 'hours' parameter to get trends from the past N hours
        # Note: We still store with timestamp and query DB for accurate "last N hours" filtering
        url = "https://serpapi.com/search.json"  # Use .json endpoint as per SerpAPI best practices
        params = {
            "engine": "google_trends_trending_now",  # Special engine for trending searches
            "api_key": serp_key,
            "geo": "",  # Empty string for worldwide (or "US" for US-specific)
            "hours": str(hours)  # Number of past hours to retrieve (e.g., "4" for last 4 hours)
        }
        
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            response = await client.get(url, params=params)
            
            # Check for errors in response
            if response.status_code != 200:
                error_data = response.text
                try:
                    error_json = response.json()
                    error_msg = error_json.get("error", error_json.get("message", error_data))
                except Exception:
                    error_msg = error_data
                if _is_serpapi_rate_limit(response.status_code, error_msg) and _get_groq_fallback_key(groq_fallback_key):
                    return await _fetch_trending_keywords_via_groq(db, hours, groq_api_key=groq_fallback_key)
                raise Exception(f"SerpAPI returned {response.status_code}: {error_msg}")
            
            data = response.json()
            
            # SerpAPI can return 200 with {"error": "message"} for API errors (e.g. invalid key, rate limit)
            if "error" in data:
                err_msg = data["error"]
                if _is_serpapi_rate_limit(200, err_msg) and _get_groq_fallback_key(groq_fallback_key):
                    return await _fetch_trending_keywords_via_groq(db, hours, groq_api_key=groq_fallback_key)
                raise Exception(f"SerpAPI error: {err_msg}")
            
            # Extract trending keywords from response
            # google_trends_trending_now engine returns trending_searches directly
            keywords = []
            
            # Parse keywords from trending_searches (primary format for trending_now engine)
            trending_searches = data.get("trending_searches", [])
            if trending_searches:
                for trend in trending_searches:
                    # Extract query field (primary field for trending searches)
                    query = trend.get("query", "")
                    if query:
                        keywords.append({
                            "keyword": query,
                            "value": trend.get("formatted_traffic", trend.get("value", 0)),
                            "timestamp": trend.get("date", datetime.utcnow().isoformat())
                        })
            
            # Fallback: Check default.trending_searches if main trending_searches is empty
            if not keywords and "default" in data:
                trending_searches = data.get("default", {}).get("trending_searches", [])
                for trend in trending_searches:
                    query = trend.get("query", "")
                    if query:
                        keywords.append({
                            "keyword": query,
                            "value": trend.get("formatted_traffic", trend.get("value", 0)),
                            "timestamp": trend.get("date", datetime.utcnow().isoformat())
                        })
            
            # If no keywords found, raise helpful error
            if not keywords:
                raise Exception(f"SerpAPI response format not recognized or no trending searches found. Response keys: {list(data.keys())}")
            
            # FIXED: Only store keywords that don't exist in the last N hours
            # This prevents duplicates and maintains accurate trending window
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            for keyword_data in keywords:
                keyword = keyword_data.get("keyword")
                if keyword:
                    # Check if this keyword already exists in the time window
                    existing = db.query(TrendingKeywords).filter(
                        TrendingKeywords.keyword == keyword,
                        TrendingKeywords.fetched_at >= cutoff_time,
                        TrendingKeywords.source == "trending",
                    ).first()
                    
                    # Only add if it doesn't exist in the current window
                    if not existing:
                        trending = TrendingKeywords(
                            keyword=keyword,
                            data=keyword_data,
                            source="trending",
                        )
                        db.add(trending)
            
            db.commit()
            
            # Return keywords from database for the last N hours (not raw API response)
            # This ensures we get accurate "last N hours" data from stored records
            return get_trending_keywords_from_db(db, hours)
            
    except httpx.HTTPError as e:
        raise Exception(f"SerpAPI request failed: {str(e)}")
    except Exception as e:
        raise Exception(f"Error fetching trending keywords: {str(e)}")


def _parse_news_date_within_days(date_str: str, max_days: int = 7) -> bool:
    """
    Parse SerpAPI news 'date' (e.g. '2 hours ago', '1 day ago', '3 days ago')
    and return True if the item is within the last max_days. Unknown formats are kept.
    """
    if not date_str or not date_str.strip():
        return True
    date_str = date_str.strip().lower()
    try:
        if "hour" in date_str or "minute" in date_str or "just now" in date_str:
            return True
        if "day" in date_str:
            parts = date_str.split()
            for i, p in enumerate(parts):
                if p == "day" or p == "days" and i > 0:
                    n = 1
                    try:
                        n = int(parts[i - 1])
                    except (ValueError, IndexError):
                        pass
                    return n <= max_days
            return True
        if "week" in date_str:
            return max_days >= 7
        # Keep "month" etc. as outside window
        if "month" in date_str or "year" in date_str:
            return False
    except Exception:
        pass
    return True


async def fetch_google_news_light(
    keyword: str,
    num_results: int = 10,
    max_days: int = 7,
    *,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch latest news from SerpAPI Google News Light (last 1-7 days).
    Uses tbs=qdr:w7 when possible; otherwise filters by parsing 'date' strings.
    """
    serp_key = (api_key or settings.serpapi_key or "").strip()
    if not serp_key:
        raise ValueError("Add your SerpAPI API key in Settings")

    await _rate_limit()

    try:
        url = "https://serpapi.com/search.json"
        params = {
            "engine": "google_news_light",
            "api_key": serp_key,
            "q": keyword,
            "num": num_results,
        }
        # Try Google date-restrict: past week (qdr:w7). If engine ignores it, we filter below.
        if max_days == 7:
            params["tbs"] = "qdr:w7"
        elif max_days == 1:
            params["tbs"] = "qdr:d1"

        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            response = await client.get(url, params=params)
            if response.status_code != 200:
                error_data = response.text
                try:
                    error_json = response.json()
                    error_msg = error_json.get("error", error_json.get("message", error_data))
                except Exception:
                    error_msg = error_data
                raise Exception(f"SerpAPI returned {response.status_code}: {error_msg}")

            data = response.json()

        raw = data.get("news_results") or []
        results = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            date_str = item.get("date") or ""
            if not _parse_news_date_within_days(date_str, max_days):
                continue
            results.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "source": item.get("source", ""),
                "thumbnail": item.get("thumbnail", ""),
                "snippet": item.get("snippet", ""),
                "date": date_str,
            })
            if len(results) >= num_results:
                break

        return results

    except httpx.HTTPError as e:
        raise Exception(f"SerpAPI request failed: {str(e)}")
    except Exception as e:
        raise Exception(f"Error fetching Google News: {str(e)}")


def _build_image_tbs(
    size: str | None = None,
    color: str | None = None,
    orientation: str | None = None,
) -> str | None:
    """Build Google Images tbs filter string for SerpAPI."""
    parts: list[str] = []

    size_map = {
        "large": "isz:l",
        "medium": "isz:m",
        "icon": "isz:i",
    }
    if size and size in size_map:
        parts.append(size_map[size])

    color_map = {
        "color": "ic:specific",
        "gray": "ic:gray",
        "transparent": "ic:trans",
    }
    if color and color in color_map:
        parts.append(color_map[color])

    orientation_map = {
        "landscape": "iar:w",
        "portrait": "iar:t",
        "square": "iar:s",
    }
    if orientation and orientation in orientation_map:
        parts.append(orientation_map[orientation])

    return ",".join(parts) if parts else None


async def search_images(
    keyword: str,
    num_results: int = 100,
    source: str | None = None,
    size: str | None = None,
    color: str | None = None,
    orientation: str | None = None,
    *,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search for images using SerpAPI"""
    serp_key = (api_key or settings.serpapi_key or "").strip()
    if not serp_key:
        raise ValueError("Add your SerpAPI API key in Settings")

    await _rate_limit()

    try:
        url = "https://serpapi.com/search"
        query = keyword
        if source and source not in ("all", "google"):
            query = f"{keyword} site:{source}.com"

        params: Dict[str, Any] = {
            "engine": "google_images",
            "api_key": serp_key,
            "q": query,
            "num": num_results,
        }

        tbs = _build_image_tbs(size=size, color=color, orientation=orientation)
        if tbs:
            params["tbs"] = tbs

        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            images = []
            if "images_results" in data:
                for img in data["images_results"]:
                    images.append({
                        "title": img.get("title", ""),
                        "link": img.get("link", ""),
                        "original": img.get("original", ""),
                        "thumbnail": img.get("thumbnail", ""),
                        "source": img.get("source", "")
                    })

            return images

    except httpx.HTTPError as e:
        raise Exception(f"SerpAPI request failed: {str(e)}")
    except Exception as e:
        raise Exception(f"Error searching images: {str(e)}")


def get_trending_keywords_from_db(
    db: Session,
    hours: int = 4,
    category: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get trending keywords from database cache, optionally filtered by category"""
    cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    query = db.query(TrendingKeywords).filter(
        TrendingKeywords.fetched_at >= cutoff_time,
        TrendingKeywords.source == "trending",
    )
    
    # Filter by category if provided
    if category:
        query = query.filter(TrendingKeywords.category == category)
    
    keywords = query.order_by(TrendingKeywords.fetched_at.desc()).all()
    
    return [{
        "id": k.id,
        "keyword": k.keyword,
        "data": k.data,
        "category": k.category,
        "fetched_at": k.fetched_at.isoformat() if k.fetched_at else None,
        "bio_blog_workflow_id": k.bio_blog_workflow_id if k.bio_blog_workflow_id is not None else k.blog_workflow_id,
        "net_worth_blog_workflow_id": k.net_worth_blog_workflow_id,
        "source": k.source,
        "kd": k.kd,
    } for k in keywords]


async def _serpapi_get(params: Dict[str, Any], *, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Shared SerpAPI GET helper with error handling."""
    serp_key = (api_key or settings.serpapi_key or "").strip()
    if not serp_key:
        raise ValueError("Add your SerpAPI API key in Settings")

    await _rate_limit()
    params = {**params, "api_key": serp_key}
    url = "https://serpapi.com/search.json"

    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(url, params=params)
        if response.status_code != 200:
            error_data = response.text
            try:
                error_json = response.json()
                error_msg = error_json.get("error", error_json.get("message", error_data))
            except Exception:
                error_msg = error_data
            raise Exception(f"SerpAPI returned {response.status_code}: {error_msg}")

        data = response.json()
        if "error" in data:
            raise Exception(f"SerpAPI error: {data['error']}")
        return data


async def fetch_google_autocomplete(
    query: str,
    limit: int = 10,
    geo: str = "US",
    hl: str = "en",
    *,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Google Autocomplete via SerpAPI (engine=google_autocomplete).
    Returns suggestions with value and optional relevance.
    """
    if not query or not query.strip():
        return []

    data = await _serpapi_get({
        "engine": "google_autocomplete",
        "q": query.strip(),
        "gl": geo.lower() if geo else "us",
        "hl": hl,
    }, api_key=api_key)

    results: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in data.get("suggestions") or []:
        if not isinstance(item, dict):
            continue
        value = (item.get("value") or "").strip()
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        results.append({
            "value": value,
            "relevance": item.get("relevance"),
            "type": item.get("type"),
        })
        if len(results) >= limit:
            break
    return results


async def fetch_google_trends_related_queries(
    query: str,
    limit: int = 10,
    geo: str = "US",
    hl: str = "en",
    *,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Google Trends related queries via SerpAPI (engine=google_trends, data_type=RELATED_QUERIES).
    Merges rising and top queries; each item includes query, value, extracted_value, link.
    """
    if not query or not query.strip():
        return []

    data = await _serpapi_get({
        "engine": "google_trends",
        "q": query.strip(),
        "data_type": "RELATED_QUERIES",
        "date": "today 12-m",
        "geo": geo.upper() if geo else "US",
        "hl": hl,
    }, api_key=api_key)

    related = data.get("related_queries") or {}
    results: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for bucket, trend_type in (("rising", "rising"), ("top", "top")):
        for item in related.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            q = (item.get("query") or "").strip()
            if not q or q.lower() in seen:
                continue
            seen.add(q.lower())
            results.append({
                "query": q,
                "value": item.get("value"),
                "extracted_value": item.get("extracted_value"),
                "link": item.get("link"),
                "type": trend_type,
            })
            if len(results) >= limit:
                return results

    return results


async def fetch_topical_mapping(
    query: str,
    limit: int = 8,
    geo: str = "US",
    hl: str = "en",
    *,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch autocomplete suggestions and Trends related queries in parallel."""
    autocomplete_task = fetch_google_autocomplete(query, limit=limit, geo=geo, hl=hl, api_key=api_key)
    related_task = fetch_google_trends_related_queries(query, limit=limit, geo=geo, hl=hl, api_key=api_key)
    autocomplete, related_queries = await asyncio.gather(
        autocomplete_task,
        related_task,
        return_exceptions=True,
    )

    errors: List[str] = []
    if isinstance(autocomplete, Exception):
        errors.append(f"Autocomplete: {autocomplete}")
        autocomplete = []
    if isinstance(related_queries, Exception):
        errors.append(f"Related queries: {related_queries}")
        related_queries = []

    if not autocomplete and not related_queries and errors:
        raise Exception("; ".join(errors))

    return {
        "query": query.strip(),
        "autocomplete_suggestions": autocomplete,
        "related_queries": related_queries,
        "errors": errors if errors else None,
    }


def get_used_trending_keywords_from_db(
    db: Session,
    category: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get all trending keywords that have a blog generated (no time limit)."""
    query = db.query(TrendingKeywords).filter(
        (
            (TrendingKeywords.bio_blog_workflow_id.isnot(None))
            | (TrendingKeywords.net_worth_blog_workflow_id.isnot(None))
            | (TrendingKeywords.blog_workflow_id.isnot(None))
        ),
        TrendingKeywords.source == "trending",
    )
    if category:
        query = query.filter(TrendingKeywords.category == category)
    keywords = query.order_by(TrendingKeywords.fetched_at.desc()).all()
    return [{
        "id": k.id,
        "keyword": k.keyword,
        "data": k.data,
        "category": k.category,
        "fetched_at": k.fetched_at.isoformat() if k.fetched_at else None,
        "bio_blog_workflow_id": k.bio_blog_workflow_id if k.bio_blog_workflow_id is not None else k.blog_workflow_id,
        "net_worth_blog_workflow_id": k.net_worth_blog_workflow_id,
        "source": k.source,
        "kd": k.kd,
    } for k in keywords]

