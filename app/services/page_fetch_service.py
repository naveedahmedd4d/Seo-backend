import asyncio
import re
from typing import Any, Optional
from urllib.parse import urlparse, unquote

import httpx

from app.services.http_ssl import httpx_verify

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
    "TrendifyPressAudit/1.0"
)

_MAX_IMAGES = 50
_MAX_BODY_BYTES = 3 * 1024 * 1024
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 15.0
_MAX_RETRIES = 2
_BLOCKED_STATUS = frozenset({401, 403, 429, 503})


def browser_headers(*, referer: Optional[str] = None) -> dict[str, str]:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def is_blocked_status(status_code: Optional[int]) -> bool:
    return status_code is not None and int(status_code) in _BLOCKED_STATUS


def classify_fetch_status(
    *,
    status_code: Optional[int],
    fetch_error: Optional[str],
) -> str:
    if fetch_error == "timeout":
        return "timeout"
    if fetch_error == "not_html":
        return "not_html"
    if fetch_error == "too_large":
        return "too_large"
    if is_blocked_status(status_code):
        return "fetch_blocked"
    if status_code is not None and int(status_code) >= 400:
        return "http_error"
    if fetch_error:
        return "http_error"
    if status_code is not None and int(status_code) < 400:
        return "ok"
    return "http_error"


def _strip_tags(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<noscript[^>]*>.*?</noscript>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _meta_content(html: str, name: str) -> Optional[str]:
    pattern = rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)["\']'
    match = re.search(pattern, html, re.I)
    if match:
        return match.group(1).strip()
    pattern_rev = rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:name|property)=["\']{re.escape(name)}["\']'
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


def _heading_texts(html: str, level: int) -> list[str]:
    pattern = rf"<h{level}[^>]*>(.*?)</h{level}>"
    return [_strip_tags(m) for m in re.findall(pattern, html, flags=re.I | re.S) if _strip_tags(m)]


def _extract_images(html: str) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for tag in re.findall(r"<img\b[^>]*>", html, flags=re.I):
        src_m = re.search(r'\bsrc=["\']([^"\']+)["\']', tag, re.I)
        if not src_m:
            continue
        alt_m = re.search(r'\balt=["\']([^"\']*)["\']', tag, re.I)
        has_alt_attr = bool(re.search(r"\balt\s*=", tag, re.I))
        alt = alt_m.group(1).strip() if alt_m else ""
        images.append(
            {
                "src": src_m.group(1).strip(),
                "alt": alt,
                "has_alt": has_alt_attr and bool(alt),
            }
        )
        if len(images) >= _MAX_IMAGES:
            break
    return images


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path or "/"
    path = path.rstrip("/") or "/"
    if path == "/":
        return ""
    segment = path.rsplit("/", 1)[-1]
    return unquote(segment)


def _paragraph_stats(body_html: str) -> tuple[int, float]:
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", body_html, flags=re.I | re.S)
    texts = [_strip_tags(p) for p in paragraphs]
    texts = [t for t in texts if t]
    if not texts:
        return 0, 0.0
    word_counts = [len(t.split()) for t in texts]
    avg = sum(word_counts) / len(word_counts) if word_counts else 0.0
    return len(texts), round(avg, 2)


def enrich_html_signals(html: str, page_url: str, *, final_url: Optional[str] = None) -> dict[str, Any]:
    """Extract extended on-page SEO signals from HTML (pure, no network)."""
    body_match = re.search(r"<body[^>]*>(.*)</body>", html, re.I | re.S)
    body_html = body_match.group(1) if body_match else html
    text = _strip_tags(body_html)
    words = text.split()
    word_count = len(words)

    h1s = _heading_texts(html, 1)
    h2s = _heading_texts(html, 2)
    images = _extract_images(html)
    paragraph_count, avg_paragraph_words = _paragraph_stats(body_html)
    list_count = len(re.findall(r"<(?:ul|ol)\b", body_html, flags=re.I))
    table_count = len(re.findall(r"<table\b", body_html, flags=re.I))
    media_count = (
        len(images)
        + len(re.findall(r"<video\b", body_html, flags=re.I))
        + len(re.findall(r"<iframe\b", body_html, flags=re.I))
    )
    html_len = max(len(body_html), 1)
    text_to_html_ratio = round(len(text) / html_len, 4)

    resolved = final_url or page_url
    return {
        "title": _title_text(html),
        "meta_description": _meta_content(html, "description"),
        "canonical": _canonical_url(html),
        "has_noindex": _has_noindex(html),
        "word_count": word_count,
        "internal_links_count": _internal_link_count(html, resolved),
        "h1": h1s,
        "h1_count": len(h1s),
        "h2_count": len(h2s),
        "images": images,
        "slug": _slug_from_url(resolved),
        "final_url": resolved,
        "paragraph_count": paragraph_count,
        "list_count": list_count,
        "table_count": table_count,
        "avg_paragraph_words": avg_paragraph_words,
        "text_to_html_ratio": text_to_html_ratio,
        "media_count": media_count,
    }


def empty_extended_signals() -> dict[str, Any]:
    return {
        "h1": [],
        "h1_count": 0,
        "h2_count": 0,
        "images": [],
        "slug": "",
        "final_url": None,
        "redirected": False,
        "paragraph_count": 0,
        "list_count": 0,
        "table_count": 0,
        "avg_paragraph_words": 0.0,
        "text_to_html_ratio": 0.0,
        "media_count": 0,
        "fetch_status": None,
        "fetch_source": None,
    }


def signals_from_html(
    html: str,
    page_url: str,
    *,
    source: str,
    status_code: int = 200,
    title_override: Optional[str] = None,
    meta_override: Optional[str] = None,
) -> dict[str, Any]:
    """Build signals dict from HTML already in hand (e.g. WP REST content)."""
    signals: dict[str, Any] = {
        "url": page_url,
        "status_code": status_code,
        "fetch_error": None,
        **empty_extended_signals(),
    }
    wrapped = html
    if "<html" not in html.lower():
        title_tag = f"<title>{title_override or ''}</title>" if title_override else ""
        meta_tag = (
            f'<meta name="description" content="{meta_override}" />' if meta_override else ""
        )
        wrapped = f"<html><head>{title_tag}{meta_tag}</head><body>{html}</body></html>"
    signals.update(enrich_html_signals(wrapped, page_url, final_url=page_url))
    if title_override:
        signals["title"] = title_override
    if meta_override:
        signals["meta_description"] = meta_override
    signals["fetch_status"] = "ok"
    signals["fetch_source"] = source
    return signals


async def fetch_page_signals(
    url: str,
    *,
    referer: Optional[str] = None,
    max_retries: int = _MAX_RETRIES,
) -> dict[str, Any]:
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
        **empty_extended_signals(),
        "fetch_source": "public_html",
    }
    if not url.startswith(("http://", "https://")):
        signals["fetch_error"] = "invalid_url"
        signals["fetch_status"] = "http_error"
        return signals

    site_origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
    headers = browser_headers(referer=referer or site_origin)
    timeout = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
    last_error: Optional[str] = None

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                verify=httpx_verify(),
                max_redirects=5,
            ) as client:
                response = await client.get(url, headers=headers)
                signals["status_code"] = response.status_code
                final_url = str(response.url)
                signals["final_url"] = final_url
                signals["redirected"] = final_url.rstrip("/") != url.rstrip("/")

                if is_blocked_status(response.status_code):
                    retry_after = response.headers.get("Retry-After")
                    if attempt < max_retries and response.status_code in (429, 503):
                        delay = 1.0 * (2**attempt)
                        if retry_after:
                            try:
                                delay = max(delay, float(retry_after))
                            except ValueError:
                                pass
                        await asyncio.sleep(min(delay, 8.0))
                        continue
                    signals["fetch_error"] = f"http_{response.status_code}"
                    signals["fetch_status"] = "fetch_blocked"
                    return signals

                if response.status_code >= 400:
                    signals["fetch_error"] = f"http_{response.status_code}"
                    signals["fetch_status"] = "http_error"
                    return signals

                content_type = (response.headers.get("content-type") or "").lower()
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    signals["fetch_error"] = "not_html"
                    signals["fetch_status"] = "not_html"
                    return signals

                raw = response.content
                if len(raw) > _MAX_BODY_BYTES:
                    signals["fetch_error"] = "too_large"
                    signals["fetch_status"] = "too_large"
                    return signals

                html = raw.decode(response.encoding or "utf-8", errors="replace")
                signals.update(enrich_html_signals(html, url, final_url=final_url))
                signals["fetch_status"] = "ok"
                signals["fetch_source"] = "public_html"
                signals["fetch_error"] = None
                return signals
        except httpx.TimeoutException:
            last_error = "timeout"
            if attempt < max_retries:
                await asyncio.sleep(1.0 * (2**attempt))
                continue
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_retries:
                await asyncio.sleep(1.0 * (2**attempt))
                continue

    signals["fetch_error"] = last_error or "fetch_failed"
    signals["fetch_status"] = "timeout" if last_error == "timeout" else "http_error"
    return signals
