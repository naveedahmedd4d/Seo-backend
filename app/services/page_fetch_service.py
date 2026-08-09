import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.services.http_ssl import httpx_verify

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _strip_tags(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _meta_content(html: str, name: str) -> Optional[str]:
    pattern = rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']'
    match = re.search(pattern, html, re.I)
    if match:
        return match.group(1).strip()
    pattern_rev = rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']{re.escape(name)}["\']'
    match = re.search(pattern_rev, html, re.I)
    return match.group(1).strip() if match else None


def _canonical_url(html: str) -> Optional[str]:
    match = re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        html,
        re.I,
    )
    if match:
        return match.group(1).strip()
    match = re.search(
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
        html,
        re.I,
    )
    return match.group(1).strip() if match else None


def _title_text(html: str) -> Optional[str]:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return _strip_tags(match.group(1)) if match else None


def _has_noindex(html: str) -> bool:
    robots = (_meta_content(html, "robots") or "").lower()
    if "noindex" in robots:
        return True
    googlebot = (_meta_content(html, "googlebot") or "").lower()
    return "noindex" in googlebot


def _internal_link_count(html: str, page_url: str) -> int:
    host = (urlparse(page_url).netloc or "").lower().replace("www.", "")
    if not host:
        return 0
    count = 0
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
        parsed = urlparse(href)
        link_host = (parsed.netloc or "").lower().replace("www.", "")
        if not link_host or link_host == host:
            count += 1
    return count


async def fetch_page_signals(url: str) -> dict[str, Any]:
    """Fetch live page HTML and extract SEO/indexing signals."""
    signals: dict[str, Any] = {
        "url": url,
        "status_code": None,
        "title": None,
        "meta_description": None,
        "canonical": None,
        "has_noindex": False,
        "word_count": 0,
        "internal_links_count": 0,
        "fetch_error": None,
    }
    if not url.startswith(("http://", "https://")):
        signals["fetch_error"] = "invalid_url"
        return signals

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            verify=httpx_verify(),
        ) as client:
            response = await client.get(url, headers={"User-Agent": _USER_AGENT})
            signals["status_code"] = response.status_code
            content_type = (response.headers.get("content-type") or "").lower()
            if response.status_code >= 400:
                signals["fetch_error"] = f"http_{response.status_code}"
                return signals
            if "text/html" not in content_type:
                signals["fetch_error"] = "not_html"
                return signals

            html = response.text
            signals["title"] = _title_text(html)
            signals["meta_description"] = _meta_content(html, "description")
            signals["canonical"] = _canonical_url(html)
            signals["has_noindex"] = _has_noindex(html)
            body_match = re.search(r"<body[^>]*>(.*)</body>", html, re.I | re.S)
            body_html = body_match.group(1) if body_match else html
            signals["word_count"] = len(_strip_tags(body_html).split())
            signals["internal_links_count"] = _internal_link_count(html, str(response.url))
    except Exception as exc:
        signals["fetch_error"] = str(exc)

    return signals
