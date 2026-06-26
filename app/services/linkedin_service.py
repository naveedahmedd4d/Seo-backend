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
from app.services.social_platform_app_service import OAuthAppCredentials, get_linkedin_app_credentials

logger = logging.getLogger(__name__)

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com"

LINKEDIN_SCOPES = "openid profile w_member_social email"


def build_authorization_url(credentials: OAuthAppCredentials, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "state": state,
        "scope": LINKEDIN_SCOPES,
    }
    return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"


async def _exchange_token(
    credentials: OAuthAppCredentials,
    data: dict[str, str],
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            LINKEDIN_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        logger.error("LinkedIn token exchange failed: %s", response.text)
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
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
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
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        },
    )


def _expires_at_from_token_payload(payload: dict[str, Any]) -> Optional[datetime]:
    expires_in = payload.get("expires_in")
    if not expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


async def fetch_userinfo(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{LINKEDIN_API_BASE}/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        logger.error("LinkedIn userinfo failed: %s", response.text)
        response.raise_for_status()
    return response.json()


def person_urn_from_userinfo(userinfo: dict[str, Any]) -> str:
    sub = userinfo.get("sub")
    if not sub:
        raise ValueError("LinkedIn profile missing subject id")
    sub_str = str(sub)
    if sub_str.startswith("urn:li:"):
        return sub_str
    return f"urn:li:person:{sub_str}"


async def create_text_share(
    access_token: str,
    *,
    person_urn: str,
    text: str,
    article_url: Optional[str] = None,
) -> dict[str, Any]:
    share_content: dict[str, Any] = {
        "shareCommentary": {"text": text[:3000]},
        "shareMediaCategory": "ARTICLE" if article_url else "NONE",
    }
    if article_url:
        share_content["media"] = [
            {
                "status": "READY",
                "originalUrl": article_url,
            }
        ]

    body = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": share_content},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{LINKEDIN_API_BASE}/v2/ugcPosts",
            json=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
    if response.status_code >= 400:
        logger.error("LinkedIn ugcPosts failed: %s", response.text)
        response.raise_for_status()
    if response.status_code == 201:
        post_id = response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id")
        return {"id": post_id}
    return response.json() if response.content else {}


async def save_linkedin_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_linkedin_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("LinkedIn app credentials not found. Connect again from Social accounts.")

    token_payload = await exchange_code_for_tokens(credentials, code)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    expires_at = _expires_at_from_token_payload(token_payload)

    userinfo = await fetch_userinfo(access_token)
    person_urn = person_urn_from_userinfo(userinfo)
    name = userinfo.get("name") or userinfo.get("email") or "LinkedIn"

    metadata: dict[str, Any] = {
        "person_urn": person_urn,
        "sub": userinfo.get("sub"),
        "name": name,
        "userinfo": userinfo,
    }

    return upsert_social_account_tokens(
        db,
        user_id,
        "linkedin",
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        account_label=str(name),
        metadata=metadata,
    )


async def get_valid_linkedin_access_token(db: Session, user_id: int) -> tuple[str, str]:
    account = get_user_social_account(db, user_id, "linkedin")
    if not account:
        raise ValueError("LinkedIn account not connected")

    credentials = get_linkedin_app_credentials(db, user_id)
    if not credentials:
        raise ValueError("LinkedIn app credentials missing")

    metadata = get_account_metadata(account)
    person_urn = str(metadata.get("person_urn") or "")
    if not person_urn:
        raise ValueError("LinkedIn profile metadata missing")

    if account.token_expires_at:
        expires_at = account.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5):
            refresh_token = get_decrypted_refresh_token(account)
            if refresh_token:
                token_payload = await refresh_access_token(credentials, refresh_token)
                upsert_social_account_tokens(
                    db,
                    user_id,
                    "linkedin",
                    access_token=token_payload["access_token"],
                    refresh_token=token_payload.get("refresh_token") or refresh_token,
                    token_expires_at=_expires_at_from_token_payload(token_payload),
                    account_label=account.account_label,
                    metadata=metadata,
                )
                return token_payload["access_token"], person_urn

    return get_decrypted_access_token(account), person_urn
