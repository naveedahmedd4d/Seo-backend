import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.services.gsc_service import encode_site_url
from app.services.http_ssl import httpx_verify

logger = logging.getLogger(__name__)

GSC_WEBMASTERS_BASE = "https://www.googleapis.com/webmasters/v3"
GSC_INSPECT_BASE = "https://search.google.com/search-console/inspect"


def build_gsc_inspect_url(site_url: str, page_url: str) -> str:
    """Build a Search Console URL Inspection deep link for manual indexing requests."""
    resource_id = quote(site_url, safe="")
    inspection_url = quote(page_url, safe="")
    return f"{GSC_INSPECT_BASE}?resource_id={resource_id}&url={inspection_url}"


def sitemap_feedpath(sitemap_url: str) -> str:
    """Extract feedpath for GSC Sitemaps API from a full sitemap URL."""
    if "://" in sitemap_url:
        from urllib.parse import urlparse

        path = urlparse(sitemap_url).path or "/"
        return path.lstrip("/")
    return sitemap_url.lstrip("/")


async def list_sitemaps(access_token: str, site_url: str) -> list[dict[str, Any]]:
    encoded = encode_site_url(site_url)
    url = f"{GSC_WEBMASTERS_BASE}/sites/{encoded}/sitemaps"
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        logger.error("GSC list sitemaps failed: %s", response.text)
        response.raise_for_status()
    return response.json().get("sitemap") or []


async def submit_sitemap(access_token: str, site_url: str, feedpath: str) -> dict[str, Any]:
    """Submit or resubmit a sitemap to Google Search Console."""
    encoded_site = encode_site_url(site_url)
    encoded_feed = quote(feedpath, safe="")
    url = f"{GSC_WEBMASTERS_BASE}/sites/{encoded_site}/sitemaps/{encoded_feed}"
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.put(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code == 403:
        return {
            "success": False,
            "status_code": 403,
            "needs_reconnect": True,
            "message": "Reconnect Google Search Console to grant sitemap submission permission.",
        }
    if response.status_code >= 400:
        logger.error("GSC submit sitemap failed: %s", response.text)
        return {
            "success": False,
            "status_code": response.status_code,
            "message": response.text,
        }
    return {"success": True, "status_code": response.status_code, "feedpath": feedpath}
