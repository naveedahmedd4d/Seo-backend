import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings
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
from app.services.social_platform_app_service import OAuthAppCredentials, get_reddit_app_credentials

logger = logging.getLogger(__name__)

REDDIT_AUTH_URL = "https://www.reddit.com/api/v1/authorize"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_BASE = "https://oauth.reddit.com"

REDDIT_SCOPES = "identity read submit"


def reddit_user_agent(client_id: str, username: Optional[str] = None) -> str:
    base = getattr(settings, "reddit_user_agent", None) or "seo-automation:reddit:v1.0"
    if username:
        return f"seo-automation:{client_id}:v1.0 (by /u/{username.lstrip('/')})"
    return base


def build_authorization_url(credentials: OAuthAppCredentials, state: str) -> str:
    params = {
        "client_id": credentials.client_id,
        "response_type": "code",
        "state": state,
        "redirect_uri": credentials.redirect_uri,
        "duration": "permanent",
        "scope": REDDIT_SCOPES,
    }
    return f"{REDDIT_AUTH_URL}?{urlencode(params)}"


def _basic_auth_header(credentials: OAuthAppCredentials) -> str:
    raw = f"{credentials.client_id}:{credentials.client_secret}"
    return base64.b64encode(raw.encode()).decode()


async def _exchange_token(
    credentials: OAuthAppCredentials,
    data: dict[str, str],
    *,
    username: Optional[str] = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            REDDIT_TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {_basic_auth_header(credentials)}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": reddit_user_agent(credentials.client_id, username),
            },
        )
    if response.status_code >= 400:
        logger.error("Reddit token exchange failed: %s", response.text)
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
    *,
    username: Optional[str] = None,
) -> dict[str, Any]:
    return await _exchange_token(
        credentials,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        username=username,
    )


def _expires_at_from_token_payload(payload: dict[str, Any]) -> Optional[datetime]:
    expires_in = payload.get("expires_in")
    if not expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


async def _api_request(
    method: str,
    path: str,
    access_token: str,
    credentials: OAuthAppCredentials,
    *,
    username: Optional[str] = None,
    data: Optional[dict[str, str]] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    url = f"{REDDIT_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": reddit_user_agent(credentials.client_id, username),
    }
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            data=data,
            params=params,
        )
    if response.status_code >= 400:
        logger.error("Reddit API %s %s failed: %s", method, path, response.text)
        response.raise_for_status()
    if response.content:
        return response.json()
    return {}


async def fetch_identity(
    access_token: str,
    credentials: OAuthAppCredentials,
) -> dict[str, Any]:
    return await _api_request("GET", "/api/v1/me", access_token, credentials)


async def fetch_subscribed_subreddits(
    access_token: str,
    credentials: OAuthAppCredentials,
    *,
    username: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    payload = await _api_request(
        "GET",
        "/subreddits/mine/subscriber",
        access_token,
        credentials,
        username=username,
        params={"limit": limit},
    )
    children = payload.get("data", {}).get("children", [])
    results: list[dict[str, Any]] = []
    for child in children:
        data = child.get("data") or {}
        name = data.get("display_name")
        if name:
            results.append(
                {
                    "name": name,
                    "title": data.get("title") or name,
                    "subscribers": data.get("subscribers"),
                }
            )
    return results


async def submit_link_post(
    access_token: str,
    credentials: OAuthAppCredentials,
    *,
    subreddit: str,
    title: str,
    url: str,
    username: Optional[str] = None,
) -> dict[str, Any]:
    sr = subreddit.strip().removeprefix("r/").removeprefix("/")
    return await _api_request(
        "POST",
        "/api/submit",
        access_token,
        credentials,
        username=username,
        data={
            "kind": "link",
            "sr": sr,
            "title": title[:300],
            "url": url,
            "resubmit": "true",
        },
    )


async def save_reddit_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_reddit_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("Reddit app credentials not found. Connect again from Social accounts.")

    token_payload = await exchange_code_for_tokens(credentials, code)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    expires_at = _expires_at_from_token_payload(token_payload)

    profile = await fetch_identity(access_token, credentials)
    username = profile.get("name") or "reddit"
    label = f"u/{username}" if not str(username).startswith("u/") else str(username)

    subreddits = await fetch_subscribed_subreddits(
        access_token,
        credentials,
        username=username,
    )
    default_sub = subreddits[0]["name"] if subreddits else None

    metadata: dict[str, Any] = {
        "username": username,
        "profile": profile,
        "subreddits": subreddits[:50],
    }
    if default_sub:
        metadata["default_subreddit"] = default_sub

    return upsert_social_account_tokens(
        db,
        user_id,
        "reddit",
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        account_label=label,
        metadata=metadata,
    )


async def get_valid_reddit_access_token(db: Session, user_id: int) -> tuple[str, OAuthAppCredentials, str]:
    account = get_user_social_account(db, user_id, "reddit")
    if not account:
        raise ValueError("Reddit account not connected")

    credentials = get_reddit_app_credentials(db, user_id)
    if not credentials:
        raise ValueError("Reddit app credentials missing")

    metadata = get_account_metadata(account)
    username = str(metadata.get("username") or "")

    if account.token_expires_at:
        expires_at = account.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2):
            refresh_token = get_decrypted_refresh_token(account)
            if refresh_token:
                token_payload = await refresh_access_token(
                    credentials,
                    refresh_token,
                    username=username or None,
                )
                upsert_social_account_tokens(
                    db,
                    user_id,
                    "reddit",
                    access_token=token_payload["access_token"],
                    refresh_token=token_payload.get("refresh_token") or refresh_token,
                    token_expires_at=_expires_at_from_token_payload(token_payload),
                    account_label=account.account_label,
                    metadata=metadata,
                )
                return token_payload["access_token"], credentials, username

    return get_decrypted_access_token(account), credentials, username


def set_default_subreddit(
    db: Session,
    account: SocialAccount,
    subreddit: str,
) -> SocialAccount:
    metadata = get_account_metadata(account)
    metadata["default_subreddit"] = subreddit.strip().removeprefix("r/").removeprefix("/")
    return update_social_account_metadata(db, account, metadata)
