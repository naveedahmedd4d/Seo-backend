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
    upsert_social_account_tokens,
)
from app.services.social_platform_app_service import (
    MastodonAppCredentials,
    get_mastodon_app_credentials,
)

logger = logging.getLogger(__name__)

MASTODON_SCOPES = "read write"


def build_authorization_url(credentials: MastodonAppCredentials, state: str) -> str:
    params = {
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "response_type": "code",
        "scope": MASTODON_SCOPES,
        "state": state,
    }
    return f"{credentials.instance_url}/oauth/authorize?{urlencode(params)}"


async def _exchange_token(
    credentials: MastodonAppCredentials,
    data: dict[str, str],
) -> dict[str, Any]:
    url = f"{credentials.instance_url}/oauth/token"
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        logger.error("Mastodon token exchange failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def exchange_code_for_tokens(
    credentials: MastodonAppCredentials,
    code: str,
) -> dict[str, Any]:
    return await _exchange_token(
        credentials,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": credentials.redirect_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        },
    )


async def refresh_access_token(
    credentials: MastodonAppCredentials,
    refresh_token: str,
) -> dict[str, Any]:
    return await _exchange_token(
        credentials,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        },
    )


def _expires_at_from_token_payload(payload: dict[str, Any]) -> Optional[datetime]:
    expires_in = payload.get("expires_in")
    if not expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


async def fetch_account_info(
    instance_url: str,
    access_token: str,
) -> dict[str, Any]:
    url = f"{instance_url}/api/v1/accounts/verify_credentials"
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        logger.error("Mastodon verify_credentials failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def upload_media(
    instance_url: str,
    access_token: str,
    image_bytes: bytes,
    filename: str = "post.png",
) -> str:
    url = f"{instance_url}/api/v2/media"
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            files={"file": (filename, image_bytes, "image/png")},
        )
    if response.status_code >= 400:
        logger.error("Mastodon media upload failed: %s", response.text)
        response.raise_for_status()
    payload = response.json()
    media_id = payload.get("id")
    if not media_id:
        raise ValueError("Mastodon media upload did not return an id")
    return str(media_id)


async def create_status(
    instance_url: str,
    access_token: str,
    *,
    status: str,
    media_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    url = f"{instance_url}/api/v1/statuses"
    data: dict[str, Any] = {"status": status[:500]}
    if media_ids:
        for index, media_id in enumerate(media_ids):
            data[f"media_ids[{index}]"] = media_id
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            data=data,
        )
    if response.status_code >= 400:
        logger.error("Mastodon status post failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def save_mastodon_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_mastodon_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("Mastodon app credentials not found. Connect again from Social accounts.")

    token_payload = await exchange_code_for_tokens(credentials, code)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    expires_at = _expires_at_from_token_payload(token_payload)

    profile = await fetch_account_info(credentials.instance_url, access_token)
    username = profile.get("username") or profile.get("acct") or "Mastodon"
    label = f"@{username}" if username and not str(username).startswith("@") else str(username)

    return upsert_social_account_tokens(
        db,
        user_id,
        "mastodon",
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        account_label=label,
        metadata={
            "username": username,
            "instance_url": credentials.instance_url,
            "profile": profile,
        },
    )


async def get_valid_mastodon_access_token(db: Session, user_id: int) -> tuple[str, str]:
    account = get_user_social_account(db, user_id, "mastodon")
    if not account:
        raise ValueError("Mastodon account not connected")

    credentials = get_mastodon_app_credentials(db, user_id)
    if not credentials:
        raise ValueError("Mastodon app credentials missing")

    metadata = get_account_metadata(account)
    instance_url = metadata.get("instance_url") or credentials.instance_url

    if account.token_expires_at:
        expires_at = account.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2):
            refresh_token = get_decrypted_refresh_token(account)
            if refresh_token:
                token_payload = await refresh_access_token(credentials, refresh_token)
                upsert_social_account_tokens(
                    db,
                    user_id,
                    "mastodon",
                    access_token=token_payload["access_token"],
                    refresh_token=token_payload.get("refresh_token") or refresh_token,
                    token_expires_at=_expires_at_from_token_payload(token_payload),
                    account_label=account.account_label,
                    metadata=metadata,
                )
                return token_payload["access_token"], instance_url

    return get_decrypted_access_token(account), instance_url
