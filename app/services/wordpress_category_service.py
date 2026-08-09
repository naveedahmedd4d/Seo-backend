"""Map app keyword categories to WordPress taxonomy and resolve category IDs."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, TYPE_CHECKING

import httpx

from app.models.wordpress_site import WordPressSite
from app.services.http_ssl import httpx_verify
from app.services.wordpress_service import decrypt_api_key

if TYPE_CHECKING:
    from app.models.trending_keywords import TrendingKeywords

logger = logging.getLogger(__name__)

PILLAR_CONTENT_WP_CATEGORY = "Pillar Content"
PILLAR_CONTENT_WP_SLUG = "pillar-content"

# App research/trending category -> WordPress category name on the target site.
APP_TO_WP_CATEGORY: Dict[str, str] = {
    "Celebrities & Public Figures": "Celebrities",
    "Sports": "Sport",
    "News & Politics": "Politicians",
}

_category_id_cache: Dict[tuple[int, str], int] = {}


def wp_category_name_for_app_category(app_category: Optional[str]) -> Optional[str]:
    """Return the WordPress category name for an app category, if mapped."""
    if not app_category or not str(app_category).strip():
        return None
    name = str(app_category).strip()
    if name.lower() == PILLAR_CONTENT_WP_CATEGORY.lower():
        return PILLAR_CONTENT_WP_CATEGORY
    return APP_TO_WP_CATEGORY.get(name)


async def resolve_wp_category_id(site: WordPressSite, wp_category_name: str) -> Optional[int]:
    """Resolve a WordPress category name to its numeric ID via the REST API."""
    name = (wp_category_name or "").strip()
    if not name:
        return None

    cache_key = (site.id, name.lower())
    if cache_key in _category_id_cache:
        return _category_id_cache[cache_key]

    url = site.url.rstrip("/")
    api_url = f"{url}/wp-json/wp/v2/categories"
    decrypted_key = decrypt_api_key(site.api_key)

    try:
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            response = await client.get(
                api_url,
                auth=(site.username, decrypted_key),
                params={"search": name, "per_page": 100},
            )
        if response.status_code != 200:
            logger.warning(
                "Failed to fetch WordPress categories for site %s: %s %s",
                site.id,
                response.status_code,
                response.text[:200],
            )
            return None

        items = response.json()
        if not isinstance(items, list):
            return None

        target = name.lower()
        for item in items:
            if not isinstance(item, dict):
                continue
            item_name = str(item.get("name", "")).strip().lower()
            if item_name == target:
                cat_id = item.get("id")
                if isinstance(cat_id, int):
                    _category_id_cache[cache_key] = cat_id
                    return cat_id

        logger.warning(
            "WordPress category %r not found on site %s (%s)",
            name,
            site.id,
            site.url,
        )
        return None
    except Exception as exc:
        logger.warning("Error resolving WordPress category %r: %s", name, exc)
        return None


async def ensure_wp_category(
    site: WordPressSite,
    wp_category_name: str,
    slug: Optional[str] = None,
) -> Optional[int]:
    """Resolve a WP category by name, creating it if missing."""
    existing = await resolve_wp_category_id(site, wp_category_name)
    if existing is not None:
        return existing

    name = (wp_category_name or "").strip()
    if not name:
        return None

    url = site.url.rstrip("/")
    api_url = f"{url}/wp-json/wp/v2/categories"
    decrypted_key = decrypt_api_key(site.api_key)
    payload = {"name": name}
    if slug:
        payload["slug"] = slug

    try:
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            response = await client.post(
                api_url,
                auth=(site.username, decrypted_key),
                json=payload,
            )
        if response.status_code not in (200, 201):
            # Race: category may already exist
            if response.status_code == 400:
                existing = await resolve_wp_category_id(site, name)
                if existing is not None:
                    return existing
            logger.warning(
                "Failed to create WordPress category %r on site %s: %s %s",
                name,
                site.id,
                response.status_code,
                response.text[:200],
            )
            return None

        data = response.json()
        if not isinstance(data, dict):
            return None
        cat_id = data.get("id")
        if isinstance(cat_id, int):
            _category_id_cache[(site.id, name.lower())] = cat_id
            return cat_id
        return None
    except Exception as exc:
        logger.warning("Error creating WordPress category %r: %s", name, exc)
        return None


async def resolve_wp_category_ids_for_app_category(
    site: WordPressSite,
    app_category: Optional[str],
) -> List[int]:
    """Map an app category to WordPress category IDs for publish payloads."""
    wp_name = wp_category_name_for_app_category(app_category)
    if not wp_name:
        return []
    if wp_name == PILLAR_CONTENT_WP_CATEGORY:
        cat_id = await ensure_wp_category(site, PILLAR_CONTENT_WP_CATEGORY, PILLAR_CONTENT_WP_SLUG)
    else:
        cat_id = await resolve_wp_category_id(site, wp_name)
    return [cat_id] if cat_id is not None else []


async def resolve_wp_category_ids_for_cluster_keyword(
    site: WordPressSite,
    row: "TrendingKeywords",
) -> List[int]:
    """
    Cluster mains → Pillar Content; cluster subs → niche category mapping.
    Non-cluster rows should not call this.
    """
    if row.parent_id is None:
        cat_id = await ensure_wp_category(site, PILLAR_CONTENT_WP_CATEGORY, PILLAR_CONTENT_WP_SLUG)
        return [cat_id] if cat_id is not None else []
    return await resolve_wp_category_ids_for_app_category(site, row.category)
