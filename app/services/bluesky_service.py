import logging
from datetime import datetime, timezone
from typing import Any, Optional

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

logger = logging.getLogger(__name__)

BSKY_API = "https://bsky.social/xrpc"


def normalize_bluesky_identifier(identifier: str) -> str:
    raw = identifier.strip()
    if not raw:
        raise ValueError("Bluesky handle or email is required")
    if "@" in raw and not raw.endswith((".bsky.social", ".host", ".social")):
        # email-style login is allowed by Bluesky
        return raw
    if raw.startswith("@"):
        raw = raw[1:]
    if "." not in raw:
        raw = f"{raw}.bsky.social"
    return raw


async def create_session(identifier: str, app_password: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{BSKY_API}/com.atproto.server.createSession",
            json={"identifier": identifier, "password": app_password},
        )
    if response.status_code >= 400:
        logger.error("Bluesky session failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def refresh_session(refresh_jwt: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{BSKY_API}/com.atproto.server.refreshSession",
            headers={"Authorization": f"Bearer {refresh_jwt}"},
        )
    if response.status_code >= 400:
        logger.error("Bluesky refresh failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def upload_blob(access_jwt: str, image_bytes: bytes) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{BSKY_API}/com.atproto.repo.uploadBlob",
            headers={
                "Authorization": f"Bearer {access_jwt}",
                "Content-Type": "image/png",
            },
            content=image_bytes,
        )
    if response.status_code >= 400:
        logger.error("Bluesky blob upload failed: %s", response.text)
        response.raise_for_status()
    return response.json().get("blob") or {}


async def create_post(
    access_jwt: str,
    *,
    did: str,
    text: str,
    embed: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "$type": "app.bsky.feed.post",
        "text": text[:300],
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if embed:
        record["embed"] = embed

    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{BSKY_API}/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {access_jwt}"},
            json={
                "repo": did,
                "collection": "app.bsky.feed.post",
                "record": record,
            },
        )
    if response.status_code >= 400:
        logger.error("Bluesky post failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def save_bluesky_account_from_credentials(
    db: Session,
    user_id: int,
    identifier: str,
    app_password: str,
) -> SocialAccount:
    normalized = normalize_bluesky_identifier(identifier)
    session = await create_session(normalized, app_password.strip())
    access_jwt = session["accessJwt"]
    refresh_jwt = session.get("refreshJwt")
    handle = session.get("handle") or normalized
    did = session.get("did")

    label = f"@{handle.lstrip('@')}" if handle else normalized

    return upsert_social_account_tokens(
        db,
        user_id,
        "bluesky",
        access_token=access_jwt,
        refresh_token=refresh_jwt,
        token_expires_at=None,
        account_label=label,
        metadata={
            "handle": handle,
            "did": did,
            "identifier": normalized,
        },
    )


async def get_valid_bluesky_session(db: Session, user_id: int) -> tuple[str, str]:
    account = get_user_social_account(db, user_id, "bluesky")
    if not account:
        raise ValueError("Bluesky account not connected")

    metadata = get_account_metadata(account)
    did = metadata.get("did")
    if not did:
        raise ValueError("Bluesky account metadata missing DID")

    refresh_jwt = get_decrypted_refresh_token(account)
    access_jwt = get_decrypted_access_token(account)

    if refresh_jwt:
        try:
            session = await refresh_session(refresh_jwt)
            access_jwt = session["accessJwt"]
            refresh_jwt = session.get("refreshJwt") or refresh_jwt
            upsert_social_account_tokens(
                db,
                user_id,
                "bluesky",
                access_token=access_jwt,
                refresh_token=refresh_jwt,
                token_expires_at=None,
                account_label=account.account_label,
                metadata=metadata,
            )
        except Exception:
            logger.warning("Bluesky session refresh failed; using stored access token")

    return access_jwt, str(did)
