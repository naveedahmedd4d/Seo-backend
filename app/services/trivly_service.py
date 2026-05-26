import httpx
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.research_history import ResearchHistory
from app.config import settings
import time
import re

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    import html

# Simple rate limiting
_last_request_time = 0
_min_request_interval = 1.0


async def _rate_limit():
    """Simple rate limiting"""
    global _last_request_time
    current_time = time.time()
    time_since_last = current_time - _last_request_time
    if time_since_last < _min_request_interval:
        import asyncio
        await asyncio.sleep(_min_request_interval - time_since_last)
    _last_request_time = time.time()


async def _fetch_url_content(url: str) -> Optional[str]:
    """Fetch content directly from a URL as fallback"""
    if not url or not url.startswith(('http://', 'https://')):
        return None
    
    try:
        await _rate_limit()
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # Set a user agent to avoid blocking
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
            # Only process HTML content
            content_type = response.headers.get('content-type', '').lower()
            if 'text/html' in content_type or 'text/plain' in content_type:
                return response.text
            return None
    except Exception as e:
        # Silently fail - this is just a fallback
        print(f"Failed to fetch content from {url}: {str(e)}")
        return None


def _extract_main_content(html_content: str) -> str:
    """Extract main content from HTML, focusing on article/body content"""
    if not html_content or not HAS_BS4:
        return html_content
    
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Remove unwanted elements
        for element in soup(["script", "style", "meta", "link", "noscript", "svg", 
                            "nav", "header", "footer", "aside", "form", "button"]):
            element.decompose()
        
        # Try to find main content areas (common patterns)
        main_content = None
        
        # Look for article tag
        article = soup.find('article')
        if article:
            main_content = article
        else:
            # Look for main tag
            main_tag = soup.find('main')
            if main_tag:
                main_content = main_tag
            else:
                # Look for content divs (common class names)
                for selector in ['#content', '#main', '.content', '.main', 
                                '.article', '.post', '.entry-content']:
                    content_div = soup.select_one(selector)
                    if content_div:
                        main_content = content_div
                        break
        
        # If we found main content, use it; otherwise use body
        if main_content:
            text = main_content.get_text(separator=' ', strip=True)
        else:
            body = soup.find('body')
            if body:
                text = body.get_text(separator=' ', strip=True)
            else:
                text = soup.get_text(separator=' ', strip=True)
        
        return text
    except Exception as e:
        print(f"Error extracting main content: {str(e)}")
        return html_content


def _is_dirty_content(content: str) -> bool:
    """Check if content appears to be raw/unparsed (contains markdown, HTML tags, etc.)"""
    if not content:
        return False
    
    # Check for markdown syntax
    markdown_patterns = [
        r'!\[.*?\]\(.*?\)',  # Markdown images
        r'#{1,6}\s+',  # Markdown headers
        r'\*\*.*?\*\*',  # Bold markdown
        r'__.*?__',  # Bold markdown alt
    ]
    
    for pattern in markdown_patterns:
        if re.search(pattern, content):
            return True
    
    # Check for HTML tags
    if re.search(r'<[^>]+>', content):
        return True
    
    # Check for excessive special characters that suggest raw format
    if content.count('![') > 2 or content.count('###') > 2:
        return True
    
    return False


def _extract_text_from_html(html_content: str) -> str:
    """Extract clean text content from HTML, markdown, or plain text"""
    if not html_content:
        return ""
    
    # Check if content looks like HTML (contains HTML tags)
    is_html = bool(re.search(r'<[^>]+>', html_content))
    
    try:
        if is_html and HAS_BS4:
            # Use BeautifulSoup to extract text from HTML
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remove script and style elements
            for script in soup(["script", "style", "meta", "link", "noscript", "svg"]):
                script.decompose()
            
            # Get text and clean it up
            text = soup.get_text(separator=' ', strip=True)
        else:
            # Not HTML or BeautifulSoup not available - treat as plain text/markdown
            text = html_content
            if not HAS_BS4:
                # Unescape HTML entities
                text = html.unescape(text)
            # Remove HTML tags if any
            text = re.sub(r'<[^>]+>', ' ', text)
        
        # Clean up markdown syntax (works for both HTML-extracted and markdown content)
        # Remove markdown image syntax: ![alt](url)
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
        
        # Remove markdown tables (keep content but simplify)
        # Replace table separators with spaces
        text = re.sub(r'\|[\s\-|:]+', ' ', text)
        # Remove remaining pipe characters
        text = re.sub(r'\s*\|\s*', ' ', text)
        
        # Remove markdown headers but keep the text (more aggressive)
        text = re.sub(r'#{1,6}\s+', '', text)
        
        # Remove markdown bold/italic but keep the text (handle nested)
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'__([^_]+)__', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'_([^_]+)_', r'\1', text)
        
        # Remove markdown links but keep the text: [text](url) -> text
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        
        # Remove markdown list markers
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
        
        # Remove markdown blockquotes
        text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
        
        # Remove markdown code blocks
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'`[^`]+`', '', text)
        
        # Remove navigation/login UI text patterns
        text = re.sub(r'(Sign in|Continue|Login|Sign up|Sign Up|Sign Me Up|No thanks|OK, got it!).*?(?=\n\n|\Z)', '', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'By (joining|continuing).*?(?=\n\n|\Z)', '', text, flags=re.IGNORECASE | re.DOTALL)
        
        # Clean up multiple whitespaces
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        
        # Remove excessive line breaks
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Remove very short lines that are likely UI elements
        lines = text.split('\n')
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            if len(line) > 3 and not line.lower() in ['skip', 'cancel', 'view all', 'load more', 'try again']:
                cleaned_lines.append(line)
        text = '\n'.join(cleaned_lines)
        
        return text.strip()
    except Exception as e:
        # If extraction fails, try simple cleanup
        print(f"Error extracting text: {str(e)}")
        try:
            text = html_content
            # Simple cleanup
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
            text = re.sub(r'#{1,6}\s+', '', text)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()
        except:
            return ""


async def research_keywords(
    db: Session,
    query: str,
    user_id: int
) -> Dict[str, Any]:
    """Research keywords using Tavily"""
    if not settings.tavily_api_key:
        raise ValueError("Tavily API key not configured")
    
    await _rate_limit()
    
    try:
        # Tavily API endpoint
        url = "https://api.tavily.com/search"
        headers = {
            "Content-Type": "application/json"
        }
        payload = {
            "api_key": settings.tavily_api_key,
            "query": query,
            "search_depth": "basic",
            "include_answer": True,
            "include_images": True,
            "include_raw_content": True
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            # Extract full content from raw_content or fetch from URLs
            if "results" in data and isinstance(data["results"], list):
                for result in data["results"]:
                    existing_content = result.get("content", "")
                    final_content = existing_content
                    
                    # Priority 1: Extract from raw_content if available
                    if "raw_content" in result and result["raw_content"]:
                        extracted_content = _extract_text_from_html(result["raw_content"])
                        if extracted_content and len(extracted_content.strip()) > 50:  # Only use if substantial
                            # Always use extracted content if:
                            # - Existing content is dirty (markdown/HTML)
                            # - Existing content is very short (< 200 chars)
                            # - Extracted content is longer
                            if (_is_dirty_content(existing_content) or 
                                len(existing_content) < 200 or
                                len(extracted_content) > len(existing_content)):
                                final_content = extracted_content
                    
                    # Priority 2: If no good content yet, fetch from URL
                    # This handles cases where raw_content is null or extraction failed
                    should_fetch_url = (
                        not final_content or 
                        len(final_content.strip()) < 200 or 
                        _is_dirty_content(final_content) or
                        (not result.get("raw_content") and len(final_content) < 500)
                    )
                    
                    if should_fetch_url and result.get("url"):
                        url_content = await _fetch_url_content(result["url"])
                        if url_content:
                            # First extract main content, then clean it
                            main_content = _extract_main_content(url_content)
                            extracted_url_content = _extract_text_from_html(main_content)
                            if extracted_url_content and len(extracted_url_content.strip()) > 100:
                                # Use URL content if it's better than what we have
                                if (len(extracted_url_content) > len(final_content) or
                                    _is_dirty_content(final_content) or
                                    len(final_content.strip()) < 200):
                                    final_content = extracted_url_content
                    
                    # Set the final content
                    result["content"] = final_content if final_content else existing_content
            
            # Store in research history
            research = ResearchHistory(
                user_id=user_id,
                query=query,
                results=data
            )
            db.add(research)
            db.commit()
            
            return data
            
    except httpx.HTTPError as e:
        raise Exception(f"Tavily API request failed: {str(e)}")
    except Exception as e:
        raise Exception(f"Error researching keywords: {str(e)}")


def _extract_youtube_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from url (youtube.com/watch?v=ID or youtu.be/ID)."""
    if not url or "youtube.com" not in url and "youtu.be" not in url:
        return None
    try:
        if "youtu.be/" in url:
            parts = url.split("youtu.be/")
            if len(parts) >= 2:
                vid = parts[1].split("?")[0].split("&")[0].strip("/")
                return vid if vid else None
        if "watch?v=" in url:
            parts = url.split("watch?v=")
            if len(parts) >= 2:
                vid = parts[1].split("&")[0].split("#")[0].strip()
                return vid if vid else None
    except Exception:
        pass
    return None


async def find_youtube_interview(celebrity_name: str) -> Optional[str]:
    """Find one relevant YouTube interview URL for the celebrity. Returns URL or None if not found."""
    videos = await search_youtube_interviews(celebrity_name)
    for v in videos:
        u = v.get("url")
        if u:
            return u
    return None


async def search_youtube_interviews(celebrity_name: str) -> List[Dict[str, Any]]:
    """
    Search for YouTube interview videos for the celebrity using Tavily.
    Returns a list of dicts with url, title, description (compatible with Apify-style response).
    """
    if not celebrity_name or not settings.tavily_api_key:
        return []
    await _rate_limit()
    query = f"{celebrity_name.strip()} interview YouTube"
    try:
        url_api = "https://api.tavily.com/search"
        payload = {
            "api_key": settings.tavily_api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": 10,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url_api, headers={"Content-Type": "application/json"}, json=payload)
            response.raise_for_status()
            data = response.json()
        seen: set = set()
        results: List[Dict[str, Any]] = []
        for result in (data.get("results") or []):
            u = (result.get("url") or "").strip()
            if not u or "youtube.com/watch" not in u and "youtu.be/" not in u:
                continue
            vid = _extract_youtube_video_id(u)
            if vid and vid in seen:
                continue
            if vid:
                seen.add(vid)
            results.append({
                "id": vid,
                "url": u,
                "title": (result.get("title") or "").strip() or None,
                "description": (result.get("content") or "").strip()[:500] if result.get("content") else None,
                "channelName": None,
                "viewCount": None,
                "date": None,
                "duration": None,
            })
        return results
    except Exception:
        return []


async def get_content_suggestions(
    query: str,
    num_suggestions: int = 5
) -> List[Dict[str, Any]]:
    """Get content suggestions using Tavily"""
    if not settings.tavily_api_key:
        raise ValueError("Tavily API key not configured")
    
    await _rate_limit()
    
    try:
        # Tavily API endpoint for content suggestions
        url = "https://api.tavily.com/search"
        headers = {
            "Content-Type": "application/json"
        }
        payload = {
            "api_key": settings.tavily_api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": num_suggestions,
            "include_answer": True,
            "include_images": False
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            # Extract results as suggestions
            suggestions = data.get("results", [])
            return suggestions[:num_suggestions]
            
    except httpx.HTTPError as e:
        raise Exception(f"Tavily API request failed: {str(e)}")
    except Exception as e:
        raise Exception(f"Error getting content suggestions: {str(e)}")


def get_research_history(
    db: Session,
    user_id: int,
    limit: int = 50
) -> List[ResearchHistory]:
    """Get research history for a user"""
    return db.query(ResearchHistory).filter(
        ResearchHistory.user_id == user_id
    ).order_by(ResearchHistory.created_at.desc()).limit(limit).all()

