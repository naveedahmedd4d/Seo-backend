import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.services.http_ssl import httpx_verify, with_ssl_retry

logger = logging.getLogger(__name__)

INSPECTION_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

NOT_INDEXED_COVERAGE_STATES = frozenset({
    "url is not on google",
    "crawled - currently not indexed",
    "discovered - currently not indexed",
    "crawled - currently not indexed",
    "duplicate, google chose different canonical than user",
    "duplicate without user-selected canonical",
    "page with redirect",
    "server error (5xx)",
    "not found (404)",
    "soft 404",
    "blocked due to access forbidden (403)",
    "blocked due to other 4xx issue",
    "blocked by robots.txt",
})

BLOCKED_INDEXING_STATES = frozenset({
    "blocked_by_meta_tag",
    "blocked_by_http_header",
})


def _parse_crawl_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def parse_inspection_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract normalized index status fields from a URL Inspection API response."""
    result = (payload.get("inspectionResult") or {})
    index_status = result.get("indexStatusResult") or {}
    return {
        "verdict": index_status.get("verdict"),
        "coverage_state": index_status.get("coverageState"),
        "indexing_state": index_status.get("indexingState"),
        "robots_txt_state": index_status.get("robotsTxtState"),
        "page_fetch_state": index_status.get("pageFetchState"),
        "google_canonical": index_status.get("googleCanonical"),
        "user_canonical": index_status.get("userCanonical"),
        "last_crawl_time": _parse_crawl_time(index_status.get("lastCrawlTime")),
        "raw_response": payload,
    }


def is_not_indexed(result: dict[str, Any]) -> bool:
    """Return True when Google reports the URL is not indexed or indexing is blocked."""
    verdict = (result.get("verdict") or "").upper()
    if verdict and verdict != "PASS":
        return True

    coverage = (result.get("coverage_state") or "").lower()
    if coverage in NOT_INDEXED_COVERAGE_STATES:
        return True
    if coverage and "not indexed" in coverage:
        return True
    if coverage and coverage.startswith("url is not on google"):
        return True

    indexing_state = (result.get("indexing_state") or "").lower()
    if indexing_state in BLOCKED_INDEXING_STATES:
        return True

    return False


async def inspect_url(access_token: str, site_url: str, inspection_url: str) -> dict[str, Any]:
    """Call Google URL Inspection API for a single URL."""
    body = {
        "inspectionUrl": inspection_url,
        "siteUrl": site_url,
        "languageCode": "en-US",
    }

    async def _post() -> httpx.Response:
        async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
            return await client.post(
                INSPECTION_URL,
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
            )

    # This runs once per URL in an audit batch, so it's the call site with the
    # most cumulative exposure to a rare transient SSL/handshake hiccup.
    response = await with_ssl_retry(_post)
    if response.status_code >= 400:
        logger.error("GSC URL inspection failed for %s: %s", inspection_url, response.text)
        response.raise_for_status()
    return parse_inspection_response(response.json())
