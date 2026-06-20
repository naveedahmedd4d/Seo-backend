import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.services.http_ssl import httpx_verify
from app.services.oauth_pkce import generate_pkce_pair
from app.services.social_account_service import (
    get_account_metadata,
    get_decrypted_access_token,
    get_decrypted_refresh_token,
    get_user_social_account,
    update_social_account_metadata,
    upsert_social_account_tokens,
)
from app.services.social_platform_app_service import OAuthAppCredentials, get_blogger_app_credentials

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
BLOGGER_API_BASE = "https://www.googleapis.com/blogger/v3"

BLOGGER_SCOPES = "https://www.googleapis.com/auth/blogger"


def build_authorization_url(
    credentials: OAuthAppCredentials,
    state: str,
    *,
    code_challenge: str,
) -> str:
    params = {
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "response_type": "code",
        "scope": BLOGGER_SCOPES,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def _exchange_token(
    credentials: OAuthAppCredentials,
    data: dict[str, str],
) -> dict[str, Any]:
    data = {**data, "client_id": credentials.client_id, "client_secret": credentials.client_secret}
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        logger.error("Blogger token exchange failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def exchange_code_for_tokens(
    credentials: OAuthAppCredentials,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    return await _exchange_token(
        credentials,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": credentials.redirect_uri,
            "code_verifier": code_verifier,
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


async def fetch_blogs(access_token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{BLOGGER_API_BASE}/users/self/blogs",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        logger.error("Blogger list blogs failed: %s", response.text)
        response.raise_for_status()
    items = response.json().get("items")
    return items if isinstance(items, list) else []


async def create_post(
    access_token: str,
    *,
    blog_id: str,
    title: str,
    content: str,
    is_draft: bool = False,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{BLOGGER_API_BASE}/blogs/{blog_id}/posts/",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"isDraft": "true" if is_draft else "false"},
            json={"title": title[:256], "content": content},
        )
    if response.status_code >= 400:
        logger.error("Blogger create post failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def save_blogger_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    code_verifier: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_blogger_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("Blogger app credentials not found. Connect again from Social accounts.")

    token_payload = await exchange_code_for_tokens(credentials, code, code_verifier)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    expires_at = _expires_at_from_token_payload(token_payload)

    blogs = await fetch_blogs(access_token)
    default_blog = blogs[0] if blogs else None
    metadata: dict[str, Any] = {
        "blogs": [
            {"id": b.get("id"), "name": b.get("name"), "url": b.get("url")}
            for b in blogs
            if b.get("id")
        ],
    }
    if default_blog and default_blog.get("id"):
        metadata["default_blog_id"] = default_blog["id"]
        metadata["default_blog_name"] = default_blog.get("name")

    label = default_blog.get("name") if default_blog else "Blogger"

    return upsert_social_account_tokens(
        db,
        user_id,
        "blogger",
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        account_label=str(label),
        metadata=metadata,
    )


async def get_valid_blogger_access_token(db: Session, user_id: int) -> str:
    account = get_user_social_account(db, user_id, "blogger")
    if not account:
        raise ValueError("Blogger account not connected")

    credentials = get_blogger_app_credentials(db, user_id)
    if not credentials:
        raise ValueError("Blogger app credentials missing")

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
                    "blogger",
                    access_token=token_payload["access_token"],
                    refresh_token=token_payload.get("refresh_token") or refresh_token,
                    token_expires_at=_expires_at_from_token_payload(token_payload),
                    account_label=account.account_label,
                    metadata=get_account_metadata(account),
                )
                return token_payload["access_token"]

    return get_decrypted_access_token(account)


def set_default_blog(
    db: Session,
    account: SocialAccount,
    blog_id: str,
    blog_name: Optional[str] = None,
) -> SocialAccount:
    metadata = get_account_metadata(account)
    metadata["default_blog_id"] = blog_id
    if blog_name:
        metadata["default_blog_name"] = blog_name
    return update_social_account_metadata(db, account, metadata)


def start_blogger_oauth_pkce() -> tuple[str, str]:
    return generate_pkce_pair()
