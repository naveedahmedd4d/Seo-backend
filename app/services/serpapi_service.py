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


def _get_groq_fallback_key() -> Optional[str]:
    """Groq API key for trending fallback (dedicated key or main groq key)."""
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
) -> List[Dict[str, Any]]:
    """Fallback: generate trending-style keywords via Groq when SerpAPI is rate limited."""
    api_key = _get_groq_fallback_key()
    if not api_key:
        raise ValueError(
            "SerpAPI rate limit hit and Groq fallback not configured. "
            "Set TRENDING_FALLBACK_GROQ_API_KEY or GROQ_API_KEY in .env"
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
        ).first()
        if not existing:
            db.add(TrendingKeywords(keyword=keyword, data=keyword_data))
    db.commit()
    return get_trending_keywords_from_db(db, hours)


async def fetch_trending_keywords(
    db: Session,
    hours: int = 4
) -> List[Dict[str, Any]]:
    """Fetch trending keywords from SerpAPI (last N hours)"""
    if not settings.serpapi_key:
        raise ValueError("SerpAPI key not configured")
    
    await _rate_limit()
    
    try:
        # SerpAPI Google Trends Trending Now endpoint
        # This engine is specifically designed for trending searches and doesn't require 'q' or 'category'
        # It supports 'hours' parameter to get trends from the past N hours
        # Note: We still store with timestamp and query DB for accurate "last N hours" filtering
        url = "https://serpapi.com/search.json"  # Use .json endpoint as per SerpAPI best practices
        params = {
            "engine": "google_trends_trending_now",  # Special engine for trending searches
            "api_key": settings.serpapi_key,
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
                if _is_serpapi_rate_limit(response.status_code, error_msg) and _get_groq_fallback_key():
                    return await _fetch_trending_keywords_via_groq(db, hours)
                raise Exception(f"SerpAPI returned {response.status_code}: {error_msg}")
            
            data = response.json()
            
            # SerpAPI can return 200 with {"error": "message"} for API errors (e.g. invalid key, rate limit)
            if "error" in data:
                err_msg = data["error"]
                if _is_serpapi_rate_limit(200, err_msg) and _get_groq_fallback_key():
                    return await _fetch_trending_keywords_via_groq(db, hours)
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
                        TrendingKeywords.fetched_at >= cutoff_time
                    ).first()
                    
                    # Only add if it doesn't exist in the current window
                    if not existing:
                        trending = TrendingKeywords(
                            keyword=keyword,
                            data=keyword_data
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
    max_days: int = 7
) -> List[Dict[str, Any]]:
    """
    Fetch latest news from SerpAPI Google News Light (last 1-7 days).
    Uses tbs=qdr:w7 when possible; otherwise filters by parsing 'date' strings.
    """
    if not settings.serpapi_key:
        raise ValueError("SerpAPI key not configured")

    await _rate_limit()

    try:
        url = "https://serpapi.com/search.json"
        params = {
            "engine": "google_news_light",
            "api_key": settings.serpapi_key,
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


async def search_images(
    keyword: str,
    num_results: int = 10
) -> List[Dict[str, Any]]:
    """Search for images using SerpAPI"""
    if not settings.serpapi_key:
        raise ValueError("SerpAPI key not configured")
    
    await _rate_limit()
    
    try:
        url = "https://serpapi.com/search"
        params = {
            "engine": "google_images",
            "api_key": settings.serpapi_key,
            "q": keyword,
            "num": num_results
        }
        
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
        TrendingKeywords.fetched_at >= cutoff_time
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
        "blog_workflow_id": k.blog_workflow_id,
    } for k in keywords]


def get_used_trending_keywords_from_db(
    db: Session,
    category: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get all trending keywords that have a blog generated (no time limit)."""
    query = db.query(TrendingKeywords).filter(
        TrendingKeywords.blog_workflow_id.isnot(None)
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
        "blog_workflow_id": k.blog_workflow_id,
    } for k in keywords]

