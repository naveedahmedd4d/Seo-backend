"""WordPress.com OAuth + article publishing for backlink outreach."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.services.http_ssl import httpx_verify
from app.services.social_account_service import (
    get_account_metadata,
    get_decrypted_access_token,
    get_decrypted_refresh_token,
    get_user_social_account,
    update_social_account_metadata,
    upsert_social_account_tokens,
)
from app.services.social_platform_app_service import (
    OAuthAppCredentials,
    get_wordpress_com_app_credentials,
)

logger = logging.getLogger(__name__)

WPCOM_AUTH_URL = "https://public-api.wordpress.com/oauth2/authorize"
WPCOM_TOKEN_URL = "https://public-api.wordpress.com/oauth2/token"
WPCOM_API_BASE = "https://public-api.wordpress.com/rest/v1.1"


def build_authorization_url(credentials: OAuthAppCredentials, state: str) -> str:
    params = {
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "response_type": "code",
        "scope": "global",
        "state": state,
    }
    return f"{WPCOM_AUTH_URL}?{urlencode(params)}"


async def _exchange_token(
    credentials: OAuthAppCredentials,
    data: dict[str, str],
) -> dict[str, Any]:
    payload = {
        **data,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
    }
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            WPCOM_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        logger.error("WordPress.com token exchange failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def exchange_code_for_tokens(
    credentials: OAuthAppCredentials,
    code: str,
) -> dict[str, Any]:
    return await _exchange_token(
        credentials,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": credentials.redirect_uri,
        },
    )


async def refresh_access_token(
    credentials: OAuthAppCredentials,
    refresh_token: str,
) -> dict[str, Any]:
    return await _exchange_token(
        credentials,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )


def _expires_at_from_token_payload(payload: dict[str, Any]) -> Optional[datetime]:
    expires_in = payload.get("expires_in")
    if not expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


async def fetch_me(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{WPCOM_API_BASE}/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        logger.error("WordPress.com /me failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def fetch_sites(access_token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{WPCOM_API_BASE}/me/sites",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        logger.error("WordPress.com /me/sites failed: %s", response.text)
        response.raise_for_status()
    payload = response.json()
    sites = payload.get("sites") if isinstance(payload, dict) else None
    if not isinstance(sites, list):
        return []
    results: list[dict[str, Any]] = []
    for site in sites:
        site_id = site.get("ID") or site.get("id")
        if not site_id:
            continue
        results.append(
            {
                "id": str(site_id),
                "name": site.get("name") or site.get("URL") or str(site_id),
                "url": site.get("URL") or site.get("URL"),
            }
        )
    return results


async def create_post(
    access_token: str,
    *,
    site_id: str,
    title: str,
    content_html: str,
    status: str = "publish",
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{WPCOM_API_BASE}/sites/{site_id}/posts/new",
            headers={"Authorization": f"Bearer {access_token}"},
            data={
                "title": title,
                "content": content_html,
                "status": status,
            },
        )
    if response.status_code >= 400:
        logger.error("WordPress.com create post failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def save_wordpress_com_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    *,
    redirect_uri: str,
) -> SocialAccount:
    credentials = get_wordpress_com_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("WordPress.com app credentials not configured")

    token_payload = await exchange_code_for_tokens(credentials, code)
    access_token = token_payload.get("access_token")
    if not access_token:
        raise ValueError("WordPress.com did not return an access token")

    me = await fetch_me(str(access_token))
    sites = await fetch_sites(str(access_token))
    default_site = sites[0] if sites else None
    username = me.get("username") or me.get("display_name") or "WordPress.com"
    metadata: dict[str, Any] = {
        "username": username,
        "user_id": me.get("ID"),
        "sites": sites,
    }
    if default_site:
        metadata["default_site_id"] = default_site["id"]
        metadata["default_site_name"] = default_site["name"]

    label = f"@{username}" if not str(username).startswith("@") else str(username)
    if default_site and default_site.get("name"):
        label = f"{label} · {default_site['name']}"

    return upsert_social_account_tokens(
        db,
        user_id,
        "wordpress_com",
        access_token=str(access_token),
        refresh_token=token_payload.get("refresh_token"),
        token_expires_at=_expires_at_from_token_payload(token_payload),
        account_label=label,
        metadata=metadata,
    )


async def get_valid_wordpress_com_access_token(db: Session, user_id: int) -> str:
    account = get_user_social_account(db, user_id, "wordpress_com")
    if not account:
        raise ValueError("WordPress.com account not connected")

    access_token = get_decrypted_access_token(account)
    expires_at = account.token_expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    still_valid = (
        expires_at is None
        or expires_at > datetime.now(timezone.utc) + timedelta(minutes=2)
    )
    if still_valid:
        return access_token

    refresh = get_decrypted_refresh_token(account)
    if not refresh:
        return access_token

    credentials = get_wordpress_com_app_credentials(db, user_id)
    if not credentials:
        return access_token

    payload = await refresh_access_token(credentials, refresh)
    new_access = payload.get("access_token")
    if not new_access:
        return access_token

    upsert_social_account_tokens(
        db,
        user_id,
        "wordpress_com",
        access_token=str(new_access),
        refresh_token=payload.get("refresh_token") or refresh,
        token_expires_at=_expires_at_from_token_payload(payload),
        account_label=account.account_label,
        metadata=get_account_metadata(account),
    )
    return str(new_access)


def set_default_site(
    db: Session,
    account: SocialAccount,
    site_id: str,
    site_name: str,
) -> SocialAccount:
    metadata = get_account_metadata(account)
    metadata["default_site_id"] = site_id
    metadata["default_site_name"] = site_name
    return update_social_account_metadata(
        db,
        account,
        metadata,
        account_label=f"{account.account_label.split(' · ')[0]} · {site_name}"
        if account.account_label
        else site_name,
    )
