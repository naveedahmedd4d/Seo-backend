import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.gsc_oauth_token import GscOAuthToken
from app.services.http_ssl import httpx_verify
from app.services.social_platform_app_service import OAuthAppCredentials
from app.services.wordpress_service import decrypt_api_key, encrypt_api_key

logger = logging.getLogger(__name__)

GSC_SCOPE = "https://www.googleapis.com/auth/webmasters"
GSC_SCOPE_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def get_gsc_redirect_uri() -> str:
    if settings.google_gsc_redirect_uri:
        return settings.google_gsc_redirect_uri.rstrip("/")
    base = settings.api_public_url.rstrip("/")
    return f"{base}/api/search-console/oauth/callback"


def build_authorization_url(state: str, credentials: OAuthAppCredentials) -> str:
    params = {
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "response_type": "code",
        "scope": GSC_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str, credentials: OAuthAppCredentials) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": credentials.redirect_uri,
            },
        )
    if response.status_code >= 400:
        logger.error("GSC token exchange failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def refresh_access_token(refresh_token: str, credentials: OAuthAppCredentials) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code >= 400:
        logger.error("GSC token refresh failed: %s", response.text)
        response.raise_for_status()
    return response.json()


def get_gsc_oauth_token(db: Session, user_id: int) -> Optional[GscOAuthToken]:
    return db.query(GscOAuthToken).filter(GscOAuthToken.user_id == user_id).first()


def save_gsc_tokens(
    db: Session,
    user_id: int,
    *,
    access_token: str,
    refresh_token: Optional[str],
    expires_in: Optional[int],
) -> GscOAuthToken:
    expires_at = None
    if expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    record = get_gsc_oauth_token(db, user_id)
    encrypted_access = encrypt_api_key(access_token)
    encrypted_refresh = encrypt_api_key(refresh_token) if refresh_token else None

    if record:
        record.access_token = encrypted_access
        if encrypted_refresh:
            record.refresh_token = encrypted_refresh
        record.token_expires_at = expires_at
        record.updated_at = datetime.now(timezone.utc)
    else:
        record = GscOAuthToken(
            user_id=user_id,
            access_token=encrypted_access,
            refresh_token=encrypted_refresh,
            token_expires_at=expires_at,
        )
        db.add(record)

    db.commit()
    db.refresh(record)
    return record


def delete_gsc_tokens(db: Session, user_id: int) -> None:
    record = get_gsc_oauth_token(db, user_id)
    if record:
        db.delete(record)
        db.commit()


def is_gsc_connected(db: Session, user_id: int) -> bool:
    record = get_gsc_oauth_token(db, user_id)
    return record is not None and bool(record.refresh_token or record.access_token)


async def get_valid_gsc_access_token(db: Session, user_id: int) -> str:
    from app.services.social_platform_app_service import get_gsc_app_credentials

    credentials = get_gsc_app_credentials(db, user_id)
    if not credentials:
        raise ValueError("Google Search Console OAuth credentials are not configured")

    record = get_gsc_oauth_token(db, user_id)
    if not record or not record.access_token:
        raise ValueError("Google Search Console is not connected")

    now = datetime.now(timezone.utc)
    expires_at = record.token_expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at and expires_at > now + timedelta(minutes=2):
        return decrypt_api_key(record.access_token)

    refresh = decrypt_api_key(record.refresh_token) if record.refresh_token else None
    if not refresh:
        if record.access_token:
            return decrypt_api_key(record.access_token)
        raise ValueError("GSC refresh token missing; reconnect Google Search Console")

    data = await refresh_access_token(refresh, credentials)
    new_access = data.get("access_token")
    if not new_access:
        raise ValueError("Failed to refresh GSC access token")

    save_gsc_tokens(
        db,
        user_id,
        access_token=new_access,
        refresh_token=data.get("refresh_token") or refresh,
        expires_in=data.get("expires_in"),
    )
    return new_access
