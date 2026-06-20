import json
import logging
from pathlib import Path
from typing import List, Optional

from pydantic import ValidationError

from app.schemas.social_platform import SocialPlatformResponse

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "social_platforms.json"

_cache_mtime: Optional[float] = None
_cache_platforms: List[SocialPlatformResponse] = []


def _load_raw_entries() -> list:
    if not REGISTRY_PATH.is_file():
        logger.error("Social platform registry not found: %s", REGISTRY_PATH)
        return []
    with REGISTRY_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        logger.error("Social platform registry must be a JSON array")
        return []
    return data


def _parse_entries(raw_entries: list) -> List[SocialPlatformResponse]:
    platforms: List[SocialPlatformResponse] = []
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            logger.warning("Skipping social platform entry at index %s: not an object", index)
            continue
        try:
            platforms.append(SocialPlatformResponse.model_validate(entry))
        except ValidationError as exc:
            logger.warning(
                "Skipping invalid social platform entry at index %s: %s",
                index,
                exc,
            )
    platforms.sort(key=lambda p: (p.sort_order, p.name.lower()))
    return platforms


def _refresh_cache_if_needed() -> None:
    global _cache_mtime, _cache_platforms
    try:
        mtime = REGISTRY_PATH.stat().st_mtime
    except OSError:
        _cache_mtime = None
        _cache_platforms = []
        return
    if _cache_mtime == mtime and _cache_platforms:
        return
    _cache_platforms = _parse_entries(_load_raw_entries())
    _cache_mtime = mtime


def get_social_platforms(
    category: Optional[str] = None,
    content_type: Optional[str] = None,
) -> List[SocialPlatformResponse]:
    """Return platforms from the registry, optionally filtered."""
    _refresh_cache_if_needed()
    platforms = list(_cache_platforms)

    if category:
        platforms = [p for p in platforms if p.category == category]

    if content_type:
        platforms = [p for p in platforms if content_type in p.content_types]

    return platforms


def clear_registry_cache() -> None:
    """Clear in-memory cache (useful for tests)."""
    global _cache_mtime, _cache_platforms
    _cache_mtime = None
    _cache_platforms = []
