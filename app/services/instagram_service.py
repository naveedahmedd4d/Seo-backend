import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.services.meta_graph_service import (
    build_meta_authorization_url,
    exchange_code_for_user_token,
    exchange_long_lived_user_token,
    fetch_instagram_username,
    fetch_user_pages,
    graph_get,
    graph_post,
)
from app.services.social_account_service import (
    get_account_metadata,
    get_decrypted_access_token,
    get_user_social_account,
    update_social_account_metadata,
    upsert_social_account_tokens,
)
from app.services.social_platform_app_service import OAuthAppCredentials, get_instagram_app_credentials

logger = logging.getLogger(__name__)

INSTAGRAM_SCOPES = (
    "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement"
)


def build_authorization_url(credentials: OAuthAppCredentials, state: str) -> str:
    return build_meta_authorization_url(credentials, state, scopes=INSTAGRAM_SCOPES)


def _expires_at_from_seconds(seconds: Optional[int]) -> Optional[datetime]:
    if not seconds:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(seconds))


async def _build_instagram_accounts(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    for page in pages:
        ig_id = page.get("instagram_business_account_id")
        page_token = page.get("access_token")
        if not ig_id or not page_token:
            continue
        username = await fetch_instagram_username(str(ig_id), str(page_token))
        accounts.append(
            {
                "id": str(ig_id),
                "username": username,
                "page_id": str(page["id"]),
                "page_name": str(page.get("name") or "Page"),
                "page_access_token": str(page_token),
            }
        )
    return accounts


async def save_instagram_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_instagram_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("Instagram app credentials not found. Connect again from Social accounts.")

    short = await exchange_code_for_user_token(credentials, code)
    short_token = short["access_token"]
    long_payload = await exchange_long_lived_user_token(credentials, short_token)
    user_token = long_payload["access_token"]
    expires_at = _expires_at_from_seconds(long_payload.get("expires_in"))

    pages = await fetch_user_pages(user_token)
    ig_accounts = await _build_instagram_accounts(pages)
    if not ig_accounts:
        raise ValueError(
            "No Instagram Business account linked to your Facebook Pages. "
            "Link an Instagram Professional account to a Page in Meta Business settings."
        )

    default = ig_accounts[0]
    metadata: dict[str, Any] = {
        "instagram_accounts": ig_accounts,
        "default_instagram_id": default["id"],
        "default_username": default["username"],
        "default_page_id": default["page_id"],
        "default_page_access_token": default["page_access_token"],
    }

    return upsert_social_account_tokens(
        db,
        user_id,
        "instagram",
        access_token=user_token,
        refresh_token=None,
        token_expires_at=expires_at,
        account_label=f"@{default['username']}",
        metadata=metadata,
    )


def _resolve_instagram_context(
    metadata: dict[str, Any],
    *,
    instagram_id: Optional[str] = None,
) -> tuple[str, str, str]:
    accounts = metadata.get("instagram_accounts") or []
    target_id = instagram_id or metadata.get("default_instagram_id")
    if not target_id:
        raise ValueError("No Instagram account selected")

    match = next((a for a in accounts if str(a.get("id")) == str(target_id)), None)
    if not match:
        raise ValueError("Instagram account not found in connected account")

    page_token = match.get("page_access_token") or metadata.get("default_page_access_token")
    if not page_token:
        raise ValueError("Instagram page access token missing. Reconnect your account.")

    return str(match["id"]), str(match.get("username") or match["id"]), str(page_token)


async def get_valid_instagram_context(
    db: Session,
    user_id: int,
    *,
    instagram_id: Optional[str] = None,
) -> tuple[str, str, str]:
    account = get_user_social_account(db, user_id, "instagram")
    if not account:
        raise ValueError("Instagram account not connected")
    metadata = get_account_metadata(account)
    if not metadata.get("instagram_accounts"):
        user_token = get_decrypted_access_token(account)
        pages = await fetch_user_pages(user_token)
        ig_accounts = await _build_instagram_accounts(pages)
        metadata["instagram_accounts"] = ig_accounts
        if ig_accounts and not metadata.get("default_instagram_id"):
            default = ig_accounts[0]
            metadata["default_instagram_id"] = default["id"]
            metadata["default_username"] = default["username"]
            metadata["default_page_id"] = default["page_id"]
            metadata["default_page_access_token"] = default["page_access_token"]
        update_social_account_metadata(db, account, metadata)
    return _resolve_instagram_context(metadata, instagram_id=instagram_id)


async def publish_instagram_image_post(
    ig_user_id: str,
    page_access_token: str,
    *,
    image_url: str,
    caption: str,
) -> dict[str, Any]:
    container = await graph_post(
        f"{ig_user_id}/media",
        page_access_token,
        data={"image_url": image_url, "caption": caption[:2200]},
    )
    creation_id = container.get("id")
    if not creation_id:
        raise ValueError("Instagram media container creation failed")

    for _ in range(10):
        status_payload = await graph_get(
            creation_id,
            page_access_token,
            params={"fields": "status_code"},
        )
        status = status_payload.get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise ValueError("Instagram media processing failed")
        await asyncio.sleep(2)

    return await graph_post(
        f"{ig_user_id}/media_publish",
        page_access_token,
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
