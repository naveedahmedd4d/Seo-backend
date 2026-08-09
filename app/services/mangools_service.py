"""Mangools KWFinder API — keyword difficulty (KD) and search volume metrics."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.services.http_ssl import httpx_verify

logger = logging.getLogger(__name__)

MANGOOLS_BASE_URL = "https://api.mangools.com/v3"
DEFAULT_LOCATION_ID = 0
DEFAULT_LANGUAGE_ID = 1000
DEFAULT_FREE_REMAINING = 5
SOFT_BATCH_CAP = 50
# Pause after limits GET so short-period rate limit (reqs-per-short-period≈3) is less likely.
SHORT_PERIOD_COOLDOWN_SEC = 1.2


class MangoolsError(Exception):
    """Raised when Mangools API calls fail."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class MangoolsQuota:
    """Normalized KWFinder keyword-lookup quota for UI + capping."""

    remaining: int
    limit: int
    reset_seconds: Optional[int] = None
    short_period_max: Optional[int] = None
    upgraded_account: bool = False
    resource_key: str = "related-keywords"


def _require_token(token: Optional[str] = None) -> str:
    t = (token or "").strip()
    if not t:
        raise MangoolsError(
            "Add your Mangools API token in Settings",
            status_code=400,
        )
    return t


def _headers(token: str) -> Dict[str, str]:
    return {
        "x-access-token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def parse_mangools_quota(limits_payload: Dict[str, Any]) -> MangoolsQuota:
    """
    Parse /kwfinder/limits for the KWFinder keyword-lookup pool.

    Prefer `related-keywords` (KWFinder seed/related lookups) — NOT
    `checked-keywords-ranks` (rank-check / SERPWatcher-scale quotas that look huge).

    Free accounts: cap remaining/limit at DEFAULT_FREE_REMAINING when Mangools
    reports an inflated adjacent pool or upgraded_account is false.
    """
    upgraded = bool(limits_payload.get("upgraded_account"))
    short_period = limits_payload.get("reqs-per-short-period")
    try:
        short_period_max = int(short_period) if short_period is not None else None
    except (TypeError, ValueError):
        short_period_max = None

    resources = limits_payload.get("resources")
    preferred_keys = (
        "related-keywords",
        "trends",
        "serps",
        # last resort only — often inflated vs free KW lookups
        "checked-keywords-ranks",
    )

    chosen_key = "related-keywords"
    remaining = DEFAULT_FREE_REMAINING
    limit = DEFAULT_FREE_REMAINING
    reset_seconds: Optional[int] = None

    if isinstance(resources, dict):
        for key in preferred_keys:
            resource = resources.get(key)
            if not isinstance(resource, dict) or "remaining" not in resource:
                continue
            try:
                remaining = max(0, int(resource["remaining"]))
                limit = max(remaining, int(resource.get("limit") or remaining))
                reset_raw = resource.get("reset")
                reset_seconds = int(reset_raw) if reset_raw is not None else None
                chosen_key = key
                break
            except (TypeError, ValueError):
                continue

    # Free-plan safety: never trust a remaining >> free daily lookups.
    if not upgraded or chosen_key == "checked-keywords-ranks":
        if remaining > DEFAULT_FREE_REMAINING or limit > DEFAULT_FREE_REMAINING * 4:
            remaining = min(remaining, DEFAULT_FREE_REMAINING)
            limit = min(limit, DEFAULT_FREE_REMAINING) if not upgraded else min(limit, remaining or DEFAULT_FREE_REMAINING)
            if not upgraded:
                limit = DEFAULT_FREE_REMAINING
                remaining = min(remaining, DEFAULT_FREE_REMAINING)

    return MangoolsQuota(
        remaining=remaining,
        limit=limit,
        reset_seconds=reset_seconds,
        short_period_max=short_period_max,
        upgraded_account=upgraded,
        resource_key=chosen_key,
    )


def parse_keyword_lookup_remaining(limits_payload: Dict[str, Any]) -> int:
    """Backward-compatible helper — returns remaining lookups only."""
    return parse_mangools_quota(limits_payload).remaining


def map_keyword_import_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map one Mangools keyword-imports row to {keyword, kd, mangools_sv}."""
    if not isinstance(item, dict):
        return None
    kw = (item.get("kw") or item.get("keyword") or "").strip()
    if not kw:
        return None

    kd_raw = item.get("seo")
    kd: Optional[int] = None
    if kd_raw is not None:
        try:
            kd = int(kd_raw)
        except (TypeError, ValueError):
            kd = None

    sv_raw = item.get("sv")
    mangools_sv: Optional[int] = None
    if sv_raw is not None:
        try:
            mangools_sv = int(sv_raw)
        except (TypeError, ValueError):
            mangools_sv = None

    return {"keyword": kw, "kd": kd, "mangools_sv": mangools_sv}


def _raise_for_status(response: httpx.Response, action: str) -> None:
    if response.status_code == 401:
        raise MangoolsError("Invalid Mangools API token", status_code=401)
    if response.status_code == 429:
        detail = (
            "Mangools rate limit or daily quota exceeded. "
            "Wait a minute (short-period limit ~3 requests) or try again after your "
            "24h keyword-lookup quota resets. Select fewer keywords next time."
        )
        raise MangoolsError(detail, status_code=429)
    if response.status_code not in (200, 201):
        raise MangoolsError(
            f"Mangools {action} failed: {response.status_code} {response.text[:300]}",
            status_code=response.status_code,
        )


async def fetch_mangools_quota(api_token: Optional[str] = None) -> MangoolsQuota:
    """GET /kwfinder/limits and return normalized quota for UI + capping."""
    token = _require_token(api_token if api_token is not None else settings.mangools_api_token)
    url = f"{MANGOOLS_BASE_URL}/kwfinder/limits"
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            response = await client.get(url, headers=_headers(token))
        _raise_for_status(response, "limits")
        data = response.json()
        if not isinstance(data, dict):
            return MangoolsQuota(
                remaining=DEFAULT_FREE_REMAINING,
                limit=DEFAULT_FREE_REMAINING,
            )
        return parse_mangools_quota(data)
    except MangoolsError:
        raise
    except Exception as exc:
        logger.warning("Mangools limits error: %s", exc)
        raise MangoolsError(f"Mangools limits error: {exc}") from exc


async def get_keyword_lookup_remaining(api_token: Optional[str] = None) -> int:
    """GET /kwfinder/limits and return remaining keyword lookups."""
    return (await fetch_mangools_quota(api_token)).remaining


async def import_keyword_metrics(
    keywords: List[str],
    location_id: int = DEFAULT_LOCATION_ID,
    language_id: int = DEFAULT_LANGUAGE_ID,
    *,
    api_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    POST /kwfinder/keyword-imports for a batch of keywords.
    Returns mapped metrics (keyword, kd, mangools_sv). kd may be None when seo is cached-null.
    """
    token = _require_token(api_token if api_token is not None else settings.mangools_api_token)
    normalized: List[str] = []
    seen: set[str] = set()
    for raw in keywords:
        text = (raw or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)

    if not normalized:
        return []

    url = f"{MANGOOLS_BASE_URL}/kwfinder/keyword-imports"
    payload = {
        "keywords": normalized,
        "location_id": location_id,
        "language_id": language_id,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
            response = await client.post(url, headers=_headers(token), json=payload)
        _raise_for_status(response, "keyword-imports")
        data = response.json()
        items = data.get("keywords") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        mapped: List[Dict[str, Any]] = []
        for item in items:
            row = map_keyword_import_item(item if isinstance(item, dict) else {})
            if row:
                mapped.append(row)
        return mapped
    except MangoolsError:
        raise
    except Exception as exc:
        logger.warning("Mangools keyword-imports error: %s", exc)
        raise MangoolsError(f"Mangools keyword-imports error: {exc}") from exc


async def cooldown_before_import() -> None:
    """Brief wait so limits GET + imports POST stay under short-period rate limit."""
    await asyncio.sleep(SHORT_PERIOD_COOLDOWN_SEC)
