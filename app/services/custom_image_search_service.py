"""Custom Image Search API (Blog Booster-compatible) for social post images."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.models.user_llm_settings import UserLlmSettings
from app.services.http_ssl import httpx_verify
from app.services.wordpress_service import decrypt_api_key

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://blog-booster.vercel.app"
SEARCH_PATH = "/api/image-search"


class CustomImageSearchError(Exception):
    """Custom image search request failed."""


@dataclass(frozen=True)
class CustomImageSearchConfig:
    api_key: str
    base_url: str


def _decrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return decrypt_api_key(value)
    except Exception:
        return None


def normalize_base_url(raw: Optional[str]) -> str:
    cleaned = (raw or "").strip().rstrip("/")
    if not cleaned:
        return DEFAULT_BASE_URL
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            "Custom Image Search base URL must be an http(s) origin, "
            "e.g. https://blog-booster.vercel.app"
        )
    # Origin only — /api/image-search is appended.
    path = (parsed.path or "").rstrip("/")
    if path and path != SEARCH_PATH:
        # Allow pasting …/api/image-search; strip it back to origin.
        if path.endswith(SEARCH_PATH):
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        raise ValueError(
            "Custom Image Search base URL should be the API origin "
            "(https://blog-booster.vercel.app), not a random path."
        )
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def load_custom_image_search_config(
    db: Session, user_id: int
) -> Optional[CustomImageSearchConfig]:
    row = db.query(UserLlmSettings).filter(UserLlmSettings.user_id == user_id).first()
    if not row:
        return None
    api_key = _decrypt(getattr(row, "custom_image_search_api_key", None))
    if not api_key:
        return None
    try:
        base_url = normalize_base_url(getattr(row, "custom_image_search_base_url", None))
    except ValueError:
        return None
    return CustomImageSearchConfig(api_key=api_key, base_url=base_url)


def is_custom_image_search_configured(db: Session, user_id: int) -> bool:
    return load_custom_image_search_config(db, user_id) is not None


def _normalize_images(payload: dict[str, Any]) -> List[dict[str, Any]]:
    """Map Blog Booster-style results to SerpAPI-like keys used by social flows."""
    raw_images = payload.get("images")
    if not isinstance(raw_images, list):
        # Single top-level url fallback
        top = payload.get("url")
        if isinstance(top, str) and top.strip().startswith(("http://", "https://")):
            return [
                {
                    "title": str(payload.get("keyword") or ""),
                    "link": top.strip(),
                    "original": top.strip(),
                    "thumbnail": top.strip(),
                    "source": "Custom Image Search",
                }
            ]
        return []

    out: List[dict[str, Any]] = []
    for item in raw_images:
        if not isinstance(item, dict):
            continue
        full = (
            item.get("full_size_url")
            or item.get("url")
            or item.get("original")
            or item.get("link")
            or ""
        )
        thumb = item.get("thumbnail_url") or item.get("thumbnail") or full
        if not isinstance(full, str) or not full.strip().startswith(("http://", "https://")):
            continue
        full = full.strip()
        thumb_s = thumb.strip() if isinstance(thumb, str) else full
        out.append(
            {
                "title": str(item.get("title") or ""),
                "link": full,
                "original": full,
                "thumbnail": thumb_s if thumb_s.startswith(("http://", "https://")) else full,
                "source": str(item.get("source") or "Custom Image Search"),
            }
        )
    return out


async def search_images_custom(
    keyword: str,
    num_results: int = 10,
    *,
    api_key: str,
    base_url: Optional[str] = None,
) -> List[dict[str, Any]]:
    """Search images via Custom Image Search API (GET /api/image-search?q=…)."""
    query = (keyword or "").strip()
    if not query:
        raise CustomImageSearchError("Search query is required")
    key = (api_key or "").strip()
    if not key:
        raise CustomImageSearchError("Custom Image Search API key is required")

    origin = normalize_base_url(base_url)
    url = f"{origin}{SEARCH_PATH}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=45.0, verify=httpx_verify()) as client:
        response = await client.get(url, params={"q": query}, headers=headers)

    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body.get("error") or body.get("detail") or body.get("message") or "")
        except Exception:  # noqa: BLE001
            detail = (response.text or "")[:200]
        suffix = f": {detail}" if detail else ""
        raise CustomImageSearchError(
            f"Custom Image Search failed ({response.status_code}){suffix}"
        )

    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise CustomImageSearchError("Custom Image Search returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CustomImageSearchError("Custom Image Search response was not an object")

    images = _normalize_images(payload)
    if num_results > 0:
        return images[:num_results]
    return images


async def search_images_for_user(
    db: Session,
    user_id: int,
    keyword: str,
    num_results: int = 10,
) -> List[dict[str, Any]]:
    cfg = load_custom_image_search_config(db, user_id)
    if not cfg:
        raise CustomImageSearchError("Custom Image Search API is not configured")
    return await search_images_custom(
        keyword,
        num_results=num_results,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
    )


async def search_social_images(
    db: Session,
    user_id: int,
    keyword: str,
    num_results: int = 10,
    *,
    serpapi_key: Optional[str] = None,
) -> List[dict[str, Any]]:
    """Prefer Custom Image Search for social posts; fall back to SerpAPI."""
    if is_custom_image_search_configured(db, user_id):
        try:
            images = await search_images_for_user(
                db, user_id, keyword, num_results=num_results
            )
            if images:
                return images
            logger.info(
                "Custom Image Search returned no images for user %s; trying SerpAPI",
                user_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Custom Image Search failed for user %s; falling back to SerpAPI: %s",
                user_id,
                exc,
            )
    key = (serpapi_key or "").strip()
    if not key:
        return []
    from app.services.serpapi_service import search_images

    return await search_images(keyword, num_results=num_results, api_key=key)


async def test_custom_image_search_connection(
    *,
    api_key: str,
    base_url: Optional[str] = None,
    query: str = "technology",
) -> tuple[bool, str]:
    try:
        images = await search_images_custom(
            query or "technology",
            num_results=3,
            api_key=api_key,
            base_url=base_url,
        )
    except (CustomImageSearchError, ValueError) as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"Custom Image Search request failed: {exc}"
    if not images:
        return False, "Connected, but no images were returned for the test query."
    return True, f"Connected — {len(images)} image(s) returned."
