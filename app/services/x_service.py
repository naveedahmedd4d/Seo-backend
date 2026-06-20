import base64
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
    upsert_social_account_tokens,
)
from app.services.social_platform_app_service import OAuthAppCredentials, get_x_app_credentials

logger = logging.getLogger(__name__)

X_AUTH_URL = "https://twitter.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
X_API_BASE = "https://api.twitter.com/2"

X_SCOPES = "tweet.read tweet.write users.read offline.access"


def build_authorization_url(
    credentials: OAuthAppCredentials,
    state: str,
    *,
    code_challenge: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "scope": X_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{X_AUTH_URL}?{urlencode(params)}"


def _basic_auth_header(credentials: OAuthAppCredentials) -> str:
    raw = f"{credentials.client_id}:{credentials.client_secret}"
    return base64.b64encode(raw.encode()).decode()


async def _exchange_token(
    credentials: OAuthAppCredentials,
    data: dict[str, str],
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            X_TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {_basic_auth_header(credentials)}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    if response.status_code >= 400:
        logger.error("X token exchange failed: %s", response.text)
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


async def fetch_user_profile(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{X_API_BASE}/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"user.fields": "username,name"},
        )
    if response.status_code >= 400:
        logger.error("X profile fetch failed: %s", response.text)
        response.raise_for_status()
    return response.json().get("data") or {}


async def create_tweet(access_token: str, text: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{X_API_BASE}/tweets",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"text": text[:280]},
        )
    if response.status_code >= 400:
        logger.error("X tweet failed: %s", response.text)
        response.raise_for_status()
    return response.json().get("data") or {}


async def save_x_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    code_verifier: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_x_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("X app credentials not found. Connect again from Social accounts.")

    token_payload = await exchange_code_for_tokens(credentials, code, code_verifier)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    expires_at = _expires_at_from_token_payload(token_payload)

    profile = await fetch_user_profile(access_token)
    username = profile.get("username") or profile.get("name") or "X"
    label = f"@{username}" if username and not str(username).startswith("@") else str(username)

    return upsert_social_account_tokens(
        db,
        user_id,
        "x",
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        account_label=label,
        metadata={"username": username, "profile": profile},
    )


async def get_valid_x_access_token(db: Session, user_id: int) -> str:
    account = get_user_social_account(db, user_id, "x")
    if not account:
        raise ValueError("X account not connected")

    credentials = get_x_app_credentials(db, user_id)
    if not credentials:
        raise ValueError("X app credentials missing")

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
                    "x",
                    access_token=token_payload["access_token"],
                    refresh_token=token_payload.get("refresh_token") or refresh_token,
                    token_expires_at=_expires_at_from_token_payload(token_payload),
                    account_label=account.account_label,
                    metadata=get_account_metadata(account),
                )
                return token_payload["access_token"]

    return get_decrypted_access_token(account)


def start_x_oauth_pkce() -> tuple[str, str]:
    return generate_pkce_pair()
