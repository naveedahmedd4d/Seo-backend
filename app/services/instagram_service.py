import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.services.http_ssl import httpx_verify
from app.services.meta_graph_service import graph_get, graph_post
from app.services.social_account_service import (
    get_account_metadata,
    get_decrypted_access_token,
    get_user_social_account,
    update_social_account_metadata,
    upsert_social_account_tokens,
)
from app.services.social_platform_app_service import OAuthAppCredentials, get_instagram_app_credentials

logger = logging.getLogger(__name__)

INSTAGRAM_GRAPH_VERSION = "v26.0"
INSTAGRAM_GRAPH_BASE = f"https://graph.instagram.com/{INSTAGRAM_GRAPH_VERSION}"
INSTAGRAM_AUTH_URL = "https://www.instagram.com/oauth/authorize"
INSTAGRAM_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
INSTAGRAM_LONG_LIVED_TOKEN_URL = "https://graph.instagram.com/access_token"
INSTAGRAM_REFRESH_TOKEN_URL = "https://graph.instagram.com/refresh_access_token"
INSTAGRAM_SCOPES = "instagram_business_basic,instagram_business_content_publish"


class InstagramGraphError(Exception):
    """An Instagram OAuth or Graph API request failed."""


def _raise_instagram_error(response: httpx.Response, action: str) -> None:
    detail: Optional[str] = None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                detail = error.get("error_user_msg") or error.get("message")
            detail = detail or payload.get("error_message")
    except Exception:  # noqa: BLE001 - an upstream error may not be JSON
        detail = None
    raise InstagramGraphError(
        f"{action} failed ({response.status_code}): {detail or 'Instagram API error'}"
    )


def build_authorization_url(credentials: OAuthAppCredentials, state: str) -> str:
    params = {
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "scope": INSTAGRAM_SCOPES,
        "response_type": "code",
        "state": state,
        # Keep the authorization experience on Instagram rather than falling
        # back to Facebook Login.
        "enable_fb_login": "0",
    }
    return f"{INSTAGRAM_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_instagram_token(
    credentials: OAuthAppCredentials,
    code: str,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            INSTAGRAM_TOKEN_URL,
            data={
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": credentials.redirect_uri,
                "code": code,
            },
        )
    if response.status_code >= 400:
        logger.error("Instagram code exchange failed: %s", response.text)
        _raise_instagram_error(response, "Instagram authorization")
    return response.json()


async def exchange_long_lived_instagram_token(
    credentials: OAuthAppCredentials,
    short_lived_token: str,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        # The long-lived token endpoint is separate from the authorization-code
        # endpoint: it accepts GET query parameters, not a POST form body.
        response = await client.get(
            INSTAGRAM_LONG_LIVED_TOKEN_URL,
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": credentials.client_secret,
                "access_token": short_lived_token,
            },
        )
    if response.status_code >= 400:
        logger.error("Instagram long-lived token exchange failed: %s", response.text)
        _raise_instagram_error(response, "Instagram long-lived token exchange")
    return response.json()


async def refresh_long_lived_instagram_token(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            INSTAGRAM_REFRESH_TOKEN_URL,
            params={
                "grant_type": "ig_refresh_token",
                "access_token": access_token,
            },
        )
    if response.status_code >= 400:
        logger.error("Instagram token refresh failed: %s", response.text)
        _raise_instagram_error(response, "Instagram token refresh")
    return response.json()


async def instagram_graph_get(
    path: str,
    access_token: str,
    *,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{INSTAGRAM_GRAPH_BASE}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            url,
            params=dict(params or {}),
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        logger.error("Instagram Graph GET %s failed: %s", path, response.text)
        _raise_instagram_error(response, f"Instagram Graph GET {path}")
    return response.json()


async def instagram_graph_post(
    path: str,
    access_token: str,
    *,
    data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{INSTAGRAM_GRAPH_BASE}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.post(
            url,
            data=dict(data or {}),
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        logger.error("Instagram Graph POST %s failed: %s", path, response.text)
        _raise_instagram_error(response, f"Instagram Graph POST {path}")
    return response.json()


def _expires_at_from_seconds(seconds: Optional[int]) -> Optional[datetime]:
    if not seconds:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(seconds))


async def save_instagram_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_instagram_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("Instagram connection is not configured. Contact your administrator.")

    short_payload = await exchange_code_for_instagram_token(credentials, code)
    short_token = short_payload.get("access_token")
    if not short_token:
        raise ValueError("Instagram did not return an access token.")

    long_payload = await exchange_long_lived_instagram_token(credentials, str(short_token))
    access_token = str(long_payload.get("access_token") or "")
    if not access_token:
        raise ValueError("Instagram did not return a long-lived access token.")

    profile = await instagram_graph_get(
        "me",
        access_token,
        params={"fields": "user_id,username,account_type"},
    )
    instagram_id = profile.get("user_id") or profile.get("id") or short_payload.get("user_id")
    if not instagram_id:
        raise ValueError("Instagram did not return an account ID.")
    username = str(profile.get("username") or instagram_id)
    instagram_id = str(instagram_id)

    metadata: dict[str, Any] = {
        "instagram_accounts": [
            {
                "id": instagram_id,
                "username": username,
                "account_type": profile.get("account_type"),
            }
        ],
        "default_instagram_id": instagram_id,
        "default_username": username,
        "auth_method": "instagram_login",
    }

    return upsert_social_account_tokens(
        db,
        user_id,
        "instagram",
        access_token=access_token,
        refresh_token=None,
        token_expires_at=_expires_at_from_seconds(long_payload.get("expires_in")),
        account_label=f"@{username}",
        metadata=metadata,
    )


def _resolve_instagram_context(
    metadata: dict[str, Any],
    account_access_token: str,
    *,
    instagram_id: Optional[str] = None,
) -> tuple[str, str, str, str]:
    accounts = metadata.get("instagram_accounts") or []
    target_id = instagram_id or metadata.get("default_instagram_id")
    if not target_id:
        raise ValueError("No Instagram account selected")

    match = next((a for a in accounts if str(a.get("id")) == str(target_id)), None)
    if not match:
        raise ValueError("Instagram account not found in connected account")

    # Preserve publishing for connections created by the older Facebook Page
    # flow until those users reconnect through Instagram Login.
    legacy_page_token = match.get("page_access_token") or metadata.get(
        "default_page_access_token"
    )
    if legacy_page_token:
        return (
            str(match["id"]),
            str(match.get("username") or match["id"]),
            str(legacy_page_token),
            "facebook",
        )

    return (
        str(match["id"]),
        str(match.get("username") or match["id"]),
        account_access_token,
        "instagram",
    )


async def get_valid_instagram_context(
    db: Session,
    user_id: int,
    *,
    instagram_id: Optional[str] = None,
) -> tuple[str, str, str, str]:
    account = get_user_social_account(db, user_id, "instagram")
    if not account:
        raise ValueError("Instagram account not connected")

    metadata = get_account_metadata(account)
    access_token = get_decrypted_access_token(account)
    is_instagram_login = metadata.get("auth_method") == "instagram_login"

    expires_at = account.token_expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if is_instagram_login and expires_at and expires_at <= datetime.now(timezone.utc) + timedelta(days=7):
        refreshed = await refresh_long_lived_instagram_token(access_token)
        access_token = str(refreshed.get("access_token") or access_token)
        account = upsert_social_account_tokens(
            db,
            user_id,
            "instagram",
            access_token=access_token,
            refresh_token=None,
            token_expires_at=_expires_at_from_seconds(refreshed.get("expires_in")),
            account_label=account.account_label,
            metadata=metadata,
        )

    return _resolve_instagram_context(
        metadata,
        access_token,
        instagram_id=instagram_id,
    )


async def publish_instagram_image_post(
    ig_user_id: str,
    access_token: str,
    *,
    image_url: str,
    caption: str,
    api_source: str = "instagram",
) -> dict[str, Any]:
    post = instagram_graph_post if api_source == "instagram" else graph_post
    get = instagram_graph_get if api_source == "instagram" else graph_get

    container = await post(
        f"{ig_user_id}/media",
        access_token,
        data={"image_url": image_url, "caption": caption[:2200]},
    )
    creation_id = container.get("id")
    if not creation_id:
        raise ValueError("Instagram media container creation failed")

    for _ in range(10):
        status_payload = await get(
            str(creation_id),
            access_token,
            params={"fields": "status_code"},
        )
        status = status_payload.get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise ValueError("Instagram media processing failed")
        await asyncio.sleep(2)

    return await post(
        f"{ig_user_id}/media_publish",
        access_token,
        data={"creation_id": creation_id},
    )


def set_default_instagram_account(
    db: Session,
    account: SocialAccount,
    instagram_id: str,
    username: str,
) -> SocialAccount:
    metadata = get_account_metadata(account)
    metadata["default_instagram_id"] = instagram_id
    metadata["default_username"] = username
    match = next(
        (a for a in (metadata.get("instagram_accounts") or []) if str(a.get("id")) == instagram_id),
        None,
    )
    if match:
        metadata["default_page_id"] = match.get("page_id")
        metadata["default_page_access_token"] = match.get("page_access_token")
    return update_social_account_metadata(db, account, metadata)
