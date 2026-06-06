"""
Humanize text via Apify actor akash9078/ai-content-humanizer (Content Humanizer).

Docs: https://apify.com/akash9078/ai-content-humanizer
Input: { "content": "<text>" }
Output dataset: humanizedContent and/or humanized
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.services.http_ssl import httpx_verify

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
APIFY_HUMANIZER_ACTOR_ID = "akash9078~ai-content-humanizer"
# Sync run waits for actor finish (seconds); actor default timeout is 3600
SYNC_TIMEOUT_SEC = 300
HTTP_TIMEOUT_SEC = 330

_OUTPUT_KEYS = (
    "humanizedContent",
    "humanized",
    "humanized_content",
    "output",
    "text",
    "result",
    "content",
)


def _extract_humanized(item: Dict[str, Any]) -> Optional[str]:
    for key in _OUTPUT_KEYS:
        val = item.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _parse_dataset_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            return [data]
        return [payload]
    return []


async def humanize_text_apify(text: str) -> str:
    """
    Run Apify Content Humanizer synchronously and return humanized text.

    Requires APIFY_API_KEY in environment / .env.
    """
    token = settings.apify_api_key
    if not token:
        raise ValueError(
            "Apify API key not configured. Set APIFY_API_KEY in your .env file."
        )

    content = str(text).strip()
    if not content:
        raise ValueError("Text is empty")

    actor_id = getattr(
        settings, "apify_humanizer_actor_id", APIFY_HUMANIZER_ACTOR_ID
    )
    url = f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    params = {"timeout": SYNC_TIMEOUT_SEC}

    logger.info("Starting Apify humanizer run (chars=%d)", len(content))

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SEC,
        verify=httpx_verify(),
    ) as client:
        response = await client.post(
            url,
            headers=headers,
            params=params,
            json={"content": content},
        )

    if response.status_code == 401:
        raise ValueError("Invalid Apify API key (401 Unauthorized)")
    if response.status_code == 402:
        raise ValueError("Apify account has insufficient credits")
    if response.status_code >= 400:
        detail = response.text[:500]
        raise RuntimeError(f"Apify humanizer failed ({response.status_code}): {detail}")

    items = _parse_dataset_items(response.json())
    if not items:
        raise RuntimeError("Apify humanizer returned an empty dataset")

    for item in items:
        humanized = _extract_humanized(item)
        if humanized:
            logger.info("Apify humanizer succeeded (output chars=%d)", len(humanized))
            return humanized

    raise RuntimeError(
        "Apify humanizer returned no humanized text. "
        f"Keys in first item: {list(items[0].keys())}"
    )
