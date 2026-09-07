"""URL discovery for Website Audit: WordPress REST + sitemap merge."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

from app.models.wordpress_site import WordPressSite
from app.services.http_ssl import httpx_verify
from app.services.wordpress_service import fetch_wp_sitemap_url, list_published_content, normalize_url_for_gsc

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 100
HARD_MAX_PAGES = 300


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_sitemap_locs(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, child_sitemap_urls) from sitemap XML."""
    page_urls: list[str] = []
    child_sitemaps: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []

    for child in list(root):
        cname = _strip_ns(child.tag).lower()
        loc_text = None
        for sub in child:
            if _strip_ns(sub.tag).lower() == "loc" and (sub.text or "").strip():
                loc_text = sub.text.strip()
                break
        if not loc_text:
            continue
        if cname == "sitemap":
            child_sitemaps.append(loc_text)
        elif cname == "url":
            page_urls.append(loc_text)
    return page_urls, child_sitemaps


async def _fetch_xml(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        resp = await client.get(url, timeout=30.0)
        if resp.status_code >= 400:
            return None
        return resp.text
    except Exception as exc:
        logger.warning("Failed to fetch sitemap %s: %s", url, exc)
        return None


async def collect_sitemap_urls(site: WordPressSite, site_url: str) -> list[str]:
    sitemap_url = await fetch_wp_sitemap_url(site)
    if not sitemap_url:
        return []

    urls: list[str] = []
    async with httpx.AsyncClient(
        verify=httpx_verify(),
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        xml_text = await _fetch_xml(client, sitemap_url)
        if not xml_text:
            return []
        page_urls, children = parse_sitemap_locs(xml_text)
        urls.extend(page_urls)
        for child in children[:20]:
            child_xml = await _fetch_xml(client, child)
            if not child_xml:
                continue
            child_pages, _ = parse_sitemap_locs(child_xml)
            urls.extend(child_pages)

    normalized: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if not u.startswith(("http://", "https://")):
            u = urljoin(site_url.rstrip("/") + "/", u)
        if u.lower().endswith(".xml"):
            continue
        nu = normalize_url_for_gsc(u, site_url)
        if nu in seen:
            continue
        seen.add(nu)
        normalized.append(nu)
    return normalized


def merge_and_cap_urls(
    wp_items: list[dict[str, Any]],
    sitemap_urls: list[str],
    *,
    max_pages: int,
    content_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    Merge WP REST items with sitemap URLs. Prefer WP metadata when present.
    Returns list of {url, wp_post_id?, post_type?, title?}.
    """
    max_pages = max(1, min(int(max_pages), HARD_MAX_PAGES))
    allowed = {t.lower() for t in content_types} if content_types else None

    by_url: dict[str, dict[str, Any]] = {}
    for item in wp_items:
        pt = (item.get("wp_post_type") or item.get("post_type") or "post").lower()
        if allowed and pt not in allowed:
            continue
        url = item["url"]
        by_url[url] = {
            "url": url,
            "wp_post_id": item.get("wp_post_id") or item.get("id"),
            "post_type": pt,
            "title": item.get("title"),
        }

    for url in sitemap_urls:
        if url in by_url:
            continue
        if allowed and len(allowed) == 1:
            # Skip sitemap-only URLs when filtering to a single content type
            continue
        by_url[url] = {
            "url": url,
            "wp_post_id": None,
            "post_type": "page",
            "title": None,
        }

    wp_first = [v for v in by_url.values() if v.get("wp_post_id") is not None]
    sm_only = [v for v in by_url.values() if v.get("wp_post_id") is None]
    return (wp_first + sm_only)[:max_pages]


async def discover_audit_urls(
    site: WordPressSite,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    content_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    site_url = site.url
    wp_items = await list_published_content(site, site_url)
    normalized_items: list[dict[str, Any]] = []
    for item in wp_items:
        normalized_items.append(
            {
                "url": item["url"],
                "wp_post_id": item.get("wp_post_id") or item.get("id"),
                "wp_post_type": item.get("wp_post_type") or item.get("type") or "post",
                "title": item.get("title"),
            }
        )

    try:
        sitemap_urls = await collect_sitemap_urls(site, site_url)
    except Exception as exc:
        logger.warning("Sitemap collection failed for site %s: %s", site.id, exc)
        sitemap_urls = []

    return merge_and_cap_urls(
        normalized_items,
        sitemap_urls,
        max_pages=max_pages,
        content_types=content_types,
    )
