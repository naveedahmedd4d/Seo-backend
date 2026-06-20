import logging
from typing import Any, Optional

import httpx

from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.services.http_ssl import httpx_verify
from app.services.social_account_service import (
    get_decrypted_access_token,
    upsert_social_account_tokens,
)

logger = logging.getLogger(__name__)

DEVTO_API_BASE = "https://dev.to/api"


async def fetch_user(api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{DEVTO_API_BASE}/users/me",
            headers={"api-key": api_key},
        )
    if response.status_code >= 400:
        logger.error("Dev.to user fetch failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def create_article(
    api_key: str,
    *,
    title: str,
    body_markdown: str,
    published: bool = True,
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "article": {
            "title": title[:128],
            "body_markdown": body_markdown,
            "published": published,
        }
    }
    if tags:
        payload["article"]["tags"] = tags[:4]

    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{DEVTO_API_BASE}/articles",
            headers={"api-key": api_key},
            json=payload,
        )
    if response.status_code >= 400:
        logger.error("Dev.to article create failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def save_devto_account_from_api_key(
    db: Session,
    user_id: int,
    api_key: str,
) -> SocialAccount:
    profile = await fetch_user(api_key.strip())
    username = profile.get("username") or profile.get("name") or "Dev.to"
    label = f"@{username}" if username and not str(username).startswith("@") else str(username)

    return upsert_social_account_tokens(
        db,
        user_id,
        "devto",
        access_token=api_key.strip(),
        refresh_token=None,
        token_expires_at=None,
        account_label=label,
        metadata={"username": username, "profile": profile},
    )


def get_devto_api_key(db: Session, user_id: int) -> str:
    from app.services.social_account_service import get_user_social_account

    account = get_user_social_account(db, user_id, "devto")
    if not account:
        raise ValueError("Dev.to account not connected")
    return get_decrypted_access_token(account)
