"""Upload-Post API client for unified social publishing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from app.config import settings
from app.schemas.social_post import SocialPostPublishResultItem
from app.services.http_ssl import httpx_verify

logger = logging.getLogger(__name__)

UPLOAD_POST_PLATFORMS = frozenset({"instagram", "facebook", "pinterest", "x"})

# Stable negative IDs for synthetic SocialAccountResponse rows (per platform).
_SYNTHETIC_ACCOUNT_IDS = {
    "instagram": -101,
    "facebook": -102,
    "pinterest": -103,
    "x": -104,
}

_UPLOAD_POST_MANAGE_USERS_URL = "https://app.upload-post.com/manage-users"


class UploadPostConfigError(Exception):
    """Raised when Upload-Post is not configured."""


class UploadPostApiError(Exception):
    """Raised when the Upload-Post API rejects a request before per-platform results."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def is_upload_post_configured() -> bool:
    return bool((settings.upload_post_api_key or "").strip())


def is_upload_post_platform(platform: str) -> bool:
    return platform.lower() in UPLOAD_POST_PLATFORMS


def upload_post_manage_users_url() -> str:
    return _UPLOAD_POST_MANAGE_USERS_URL


def _auth_headers() -> dict[str, str]:
    key = (settings.upload_post_api_key or "").strip()
    if not key:
        raise UploadPostConfigError("UPLOAD_POST_API_KEY is not configured")
    return {"Authorization": f"Apikey {key}"}


def _base_url() -> str:
    return (settings.upload_post_base_url or "https://api.upload-post.com").rstrip("/")


def _profile_username() -> str:
    return (settings.upload_post_user or "trendifypress").strip() or "trendifypress"


def _is_connected_account(value: Any) -> bool:
    """Upload-Post marks unconnected platforms as '', null, or empty placeholders."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        # Connected accounts usually include display_name / username / social_images
        if any(
            value.get(k)
            for k in ("display_name", "username", "social_images", "id", "name")
        ):
            return True
        # Non-empty dict with any truthy value counts as connected
        return any(bool(v) for v in value.values())
    return bool(value)


def _account_display_label(platform: str, value: Any) -> str:
    username = _profile_username()
    display: str | None = None
    if isinstance(value, dict):
        for key in ("display_name", "username", "name"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                display = raw.strip()
                break
    elif isinstance(value, str) and value.strip():
        display = value.strip()
    if display:
        return f"{display} · Upload-Post"
    return f"{username} · Upload-Post"


def _extract_external_id(platform_result: dict[str, Any]) -> str | None:
    for key in (
        "publish_id",
        "post_id",
        "container_id",
        "id",
        "tweet_id",
        "pin_id",
        "media_id",
    ):
        raw = platform_result.get(key)
        if raw is not None and str(raw).strip():
            return str(raw)
    return None


def _extract_external_url(platform_result: dict[str, Any]) -> str | None:
    for key in ("url", "permalink", "post_url", "share_url", "link"):
        raw = platform_result.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def map_platform_results(
    platforms: list[str],
    results: dict[str, Any] | None,
    *,
    request_error: str | None = None,
) -> list[SocialPostPublishResultItem]:
    """Map Upload-Post `results` dict into SocialPostPublishResultItem list."""
    items: list[SocialPostPublishResultItem] = []
    results = results or {}
    for platform in platforms:
        platform_key = platform.lower()
        if request_error:
            items.append(
                SocialPostPublishResultItem(
                    platform=platform_key,
                    success=False,
                    error=request_error,
                )
            )
            continue
        raw = results.get(platform_key)
        if raw is None:
            # Some responses nest under slightly different keys
            raw = results.get(platform)
        if not isinstance(raw, dict):
            items.append(
                SocialPostPublishResultItem(
                    platform=platform_key,
                    success=False,
                    error="No result returned for this platform",
                )
            )
            continue
        success = bool(raw.get("success"))
        error = None if success else (raw.get("error") or raw.get("message") or "Publish failed")
        if isinstance(error, dict):
            error = error.get("message") or str(error)
        items.append(
            SocialPostPublishResultItem(
                platform=platform_key,
                success=success,
                external_id=_extract_external_id(raw) if success else None,
                external_url=_extract_external_url(raw) if success else None,
                error=str(error) if error and not success else None,
            )
        )
    return items


async def get_profile(username: str | None = None) -> dict[str, Any] | None:
    """Fetch a single Upload-Post profile by username, or None if missing."""
    if not is_upload_post_configured():
        return None
    target = (username or _profile_username()).strip()
    url = f"{_base_url()}/api/uploadposts/users/{target}"
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            response = await client.get(url, headers=_auth_headers())
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            logger.warning(
                "Upload-Post get_profile failed: %s %s",
                response.status_code,
                response.text[:300],
            )
            return None
        data = response.json()
        profile = data.get("profile") if isinstance(data, dict) else None
        if isinstance(profile, dict):
            return profile
        # Fallback: list endpoint and find by username
        return await _find_profile_in_list(target)
    except Exception as exc:
        logger.warning("Upload-Post get_profile error: %s", exc)
        return None


async def _find_profile_in_list(username: str) -> dict[str, Any] | None:
    url = f"{_base_url()}/api/uploadposts/users"
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(url, headers=_auth_headers())
    if response.status_code >= 400:
        return None
    data = response.json()
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, list):
        return None
    for profile in profiles:
        if isinstance(profile, dict) and profile.get("username") == username:
            return profile
    return None


async def get_connected_platforms(
    username: str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Return connected Upload-Post platforms for our shared profile.

    Keys are platform ids in UPLOAD_POST_PLATFORMS; values include label metadata.
    """
    profile = await get_profile(username)
    if not profile:
        return {}
    social_accounts = profile.get("social_accounts") or {}
    if not isinstance(social_accounts, dict):
        return {}
    connected: dict[str, dict[str, Any]] = {}
    for platform in UPLOAD_POST_PLATFORMS:
        value = social_accounts.get(platform)
        if not _is_connected_account(value):
            continue
        connected[platform] = {
            "label": _account_display_label(platform, value),
            "raw": value if isinstance(value, dict) else {"value": value},
            "synthetic_id": _SYNTHETIC_ACCOUNT_IDS[platform],
        }
    return connected


def build_synthetic_account_responses(
    connected: dict[str, dict[str, Any]],
    *,
    pinterest_board_id: str | None = None,
    facebook_page_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build dicts matching SocialAccountResponse for Upload-Post-backed platforms."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for platform, info in connected.items():
        metadata: dict[str, Any] = {
            "managed_by": "upload_post",
            "upload_post_user": _profile_username(),
            "manage_url": _UPLOAD_POST_MANAGE_USERS_URL,
        }
        raw = info.get("raw")
        if isinstance(raw, dict):
            metadata["upload_post_account"] = raw
        if platform == "pinterest" and pinterest_board_id:
            metadata["default_board_id"] = pinterest_board_id
        if platform == "facebook" and facebook_page_id:
            metadata["default_page_id"] = facebook_page_id
        rows.append(
            {
                "id": int(info["synthetic_id"]),
                "platform": platform,
                "account_label": info.get("label") or f"{_profile_username()} · Upload-Post",
                "connected": True,
                "metadata": metadata,
                "created_at": now,
                "updated_at": now,
            }
        )
    return rows


async def get_pinterest_boards(profile: str | None = None) -> list[dict[str, str]]:
    if not is_upload_post_configured():
        return []
    url = f"{_base_url()}/api/uploadposts/pinterest/boards"
    params = {"profile": profile or _profile_username()}
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            response = await client.get(url, headers=_auth_headers(), params=params)
        if response.status_code >= 400:
            logger.warning("Upload-Post pinterest boards failed: %s", response.text[:300])
            return []
        data = response.json()
        boards = data.get("boards") if isinstance(data, dict) else None
        if not isinstance(boards, list):
            return []
        out: list[dict[str, str]] = []
        for board in boards:
            if not isinstance(board, dict):
                continue
            board_id = board.get("id")
            name = board.get("name") or str(board_id or "")
            if board_id is not None:
                out.append({"id": str(board_id), "name": str(name)})
        return out
    except Exception as exc:
        logger.warning("Upload-Post pinterest boards error: %s", exc)
        return []


async def get_facebook_pages(profile: str | None = None) -> list[dict[str, str]]:
    if not is_upload_post_configured():
        return []
    url = f"{_base_url()}/api/uploadposts/facebook/pages"
    params = {"profile": profile or _profile_username()}
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            response = await client.get(url, headers=_auth_headers(), params=params)
        if response.status_code >= 400:
            logger.warning("Upload-Post facebook pages failed: %s", response.text[:300])
            return []
        data = response.json()
        pages = data.get("pages") if isinstance(data, dict) else None
        if not isinstance(pages, list):
            return []
        out: list[dict[str, str]] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_id = page.get("id")
            name = page.get("name") or str(page_id or "")
            if page_id is not None:
                out.append({"id": str(page_id), "name": str(name)})
        return out
    except Exception as exc:
        logger.warning("Upload-Post facebook pages error: %s", exc)
        return []


async def _poll_upload_status(
    client: httpx.AsyncClient,
    request_id: str,
    *,
    max_attempts: int = 12,
    delay_seconds: float = 2.0,
) -> dict[str, Any]:
    url = f"{_base_url()}/api/uploadposts/status"
    last: dict[str, Any] = {}
    for _ in range(max_attempts):
        response = await client.get(
            url,
            headers=_auth_headers(),
            params={"request_id": request_id},
        )
        if response.status_code >= 400:
            raise UploadPostApiError(
                f"Upload status check failed: {response.text[:300]}",
                status_code=response.status_code,
            )
        last = response.json() if response.content else {}
        if not isinstance(last, dict):
            last = {}
        status_val = str(last.get("status") or last.get("state") or "").lower()
        if last.get("results") or status_val in {
            "completed",
            "complete",
            "done",
            "success",
            "failed",
            "error",
        }:
            return last
        await asyncio.sleep(delay_seconds)
    return last


async def upload_photos(
    platforms: list[str],
    image_bytes: bytes,
    *,
    title: str,
    description: Optional[str] = None,
    platform_titles: Optional[dict[str, str]] = None,
    pinterest_board_id: Optional[str] = None,
    facebook_page_id: Optional[str] = None,
    filename: str = "social-post.png",
) -> list[SocialPostPublishResultItem]:
    """
    Publish a photo to one or more Upload-Post platforms in a single request.

    Uses multipart upload of image bytes (works without a public media URL).
    """
    if not platforms:
        return []
    if not image_bytes:
        return [
            SocialPostPublishResultItem(
                platform=p.lower(),
                success=False,
                error="Image bytes are required for Upload-Post photo publish",
            )
            for p in platforms
        ]

    normalized = [p.lower() for p in platforms]
    unsupported = [p for p in normalized if p not in UPLOAD_POST_PLATFORMS]
    if unsupported:
        raise UploadPostApiError(f"Unsupported Upload-Post platforms: {unsupported}")

    early_failures: list[SocialPostPublishResultItem] = []
    board_id = (pinterest_board_id or settings.upload_post_pinterest_board_id or "").strip()
    if "pinterest" in normalized and not board_id:
        early_failures.append(
            SocialPostPublishResultItem(
                platform="pinterest",
                success=False,
                error=(
                    "pinterest_board_id is required. Set UPLOAD_POST_PINTEREST_BOARD_ID "
                    "or pass platform_options.pinterest.board_id."
                ),
            )
        )
        normalized = [p for p in normalized if p != "pinterest"]
        if not normalized:
            return early_failures

    form_fields: list[tuple[str, str]] = [
        ("user", _profile_username()),
        ("title", title or " "),
    ]
    for platform in normalized:
        form_fields.append(("platform[]", platform))

    if description:
        form_fields.append(("description", description))

    platform_titles = platform_titles or {}
    title_field_map = {
        "instagram": "instagram_title",
        "facebook": "facebook_title",
        "x": "x_title",
        "pinterest": "pinterest_title",
    }
    for platform, field in title_field_map.items():
        value = platform_titles.get(platform)
        if value:
            form_fields.append((field, value))

    if "pinterest" in normalized and board_id:
        form_fields.append(("pinterest_board_id", board_id))

    page_id = (facebook_page_id or settings.upload_post_facebook_page_id or "").strip()
    if "facebook" in normalized and page_id:
        form_fields.append(("facebook_page_id", page_id))

    # Put text + file fields in `files=` — httpx mishandles data=list + files=
    # (TypeError: expected bytes-like object, tuple found).
    multipart: list[tuple[str, Any]] = [
        (key, (None, value)) for key, value in form_fields
    ]
    multipart.append(("photos[]", (filename, image_bytes, "image/png")))

    url = f"{_base_url()}/api/upload_photos"
    headers = _auth_headers()
    verify = httpx_verify()

    def _post_sync() -> httpx.Response:
        # httpx AsyncClient rejects multipart SyncByteStream ("Attempted to send
        # an sync request with an AsyncClient instance"). Use sync Client instead.
        with httpx.Client(timeout=120.0, verify=verify) as sync_client:
            return sync_client.post(
                url,
                headers=headers,
                files=multipart,
            )

    try:
        response = await asyncio.to_thread(_post_sync)
        if response.status_code >= 400:
            message = _error_message_from_response(response)
            raise UploadPostApiError(message, status_code=response.status_code)

        data = response.json() if response.content else {}
        if not isinstance(data, dict):
            data = {}

        # Async job: poll status with AsyncClient (GET, no multipart)
        if data.get("results") is None and data.get("request_id"):
            async with httpx.AsyncClient(timeout=120.0, verify=verify) as client:
                data = await _poll_upload_status(client, str(data["request_id"]))

        if data.get("success") is False and not data.get("results"):
            message = str(data.get("message") or "Upload-Post request failed")
            return early_failures + map_platform_results(
                normalized, None, request_error=message
            )

        return early_failures + map_platform_results(normalized, data.get("results"))
    except UploadPostApiError as exc:
        return early_failures + map_platform_results(
            normalized, None, request_error=str(exc)
        )
    except Exception as exc:
        logger.exception("Upload-Post upload_photos failed")
        return early_failures + map_platform_results(
            normalized, None, request_error=str(exc)
        )


def _error_message_from_response(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            msg = data.get("message") or data.get("error") or data.get("detail")
            if msg:
                return str(msg)
    except Exception:
        pass
    text = (response.text or "").strip()
    if text:
        return f"Upload-Post HTTP {response.status_code}: {text[:400]}"
    return f"Upload-Post HTTP {response.status_code}"
