import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.services.meta_graph_service import (
    build_meta_authorization_url,
    exchange_code_for_user_token,
    exchange_long_lived_user_token,
    fetch_user_pages,
    graph_post,
)
from app.services.social_account_service import (
    get_account_metadata,
    get_decrypted_access_token,
    get_user_social_account,
    update_social_account_metadata,
    upsert_social_account_tokens,
)
from app.services.social_platform_app_service import OAuthAppCredentials, get_facebook_app_credentials

logger = logging.getLogger(__name__)

FACEBOOK_SCOPES = "pages_show_list,pages_manage_posts,pages_read_engagement"


def build_authorization_url(credentials: OAuthAppCredentials, state: str) -> str:
    return build_meta_authorization_url(credentials, state, scopes=FACEBOOK_SCOPES)


def _expires_at_from_seconds(seconds: Optional[int]) -> Optional[datetime]:
    if not seconds:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(seconds))


async def save_facebook_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_facebook_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("Facebook app credentials not found. Connect again from Social accounts.")

    short = await exchange_code_for_user_token(credentials, code)
    short_token = short["access_token"]
    long_payload = await exchange_long_lived_user_token(credentials, short_token)
    user_token = long_payload["access_token"]
    expires_at = _expires_at_from_seconds(long_payload.get("expires_in"))

    pages = await fetch_user_pages(user_token)
    if not pages:
        raise ValueError(
            "No Facebook Pages found. Create a Page and grant access during login."
        )

    default_page = pages[0]
    metadata: dict[str, Any] = {
        "pages": pages,
        "default_page_id": default_page["id"],
        "default_page_name": default_page["name"],
        "default_page_access_token": default_page.get("access_token"),
    }

    return upsert_social_account_tokens(
        db,
        user_id,
        "facebook",
        access_token=user_token,
        refresh_token=None,
        token_expires_at=expires_at,
        account_label=str(default_page["name"]),
        metadata=metadata,
    )


def _resolve_page_context(
    metadata: dict[str, Any],
    *,
    page_id: Optional[str] = None,
) -> tuple[str, str, str]:
    pages = metadata.get("pages") or []
    target_id = page_id or metadata.get("default_page_id")
    if not target_id:
        raise ValueError("No Facebook Page selected")

    match = next((p for p in pages if str(p.get("id")) == str(target_id)), None)
    if not match:
        raise ValueError("Facebook Page not found in connected account")

    page_token = match.get("access_token") or metadata.get("default_page_access_token")
    if not page_token:
        raise ValueError("Facebook Page access token missing. Reconnect your account.")

    return str(match["id"]), str(match.get("name") or "Page"), str(page_token)


async def get_valid_facebook_page_context(
    db: Session,
    user_id: int,
    *,
    page_id: Optional[str] = None,
) -> tuple[str, str, str]:
    account = get_user_social_account(db, user_id, "facebook")
    if not account:
        raise ValueError("Facebook account not connected")
    metadata = get_account_metadata(account)
    if not metadata.get("pages"):
        user_token = get_decrypted_access_token(account)
        pages = await fetch_user_pages(user_token)
        metadata["pages"] = pages
        if pages and not metadata.get("default_page_id"):
            metadata["default_page_id"] = pages[0]["id"]
            metadata["default_page_name"] = pages[0]["name"]
            metadata["default_page_access_token"] = pages[0].get("access_token")
        update_social_account_metadata(db, account, metadata)
    return _resolve_page_context(metadata, page_id=page_id)


async def publish_page_post(
    page_access_token: str,
    *,
    page_id: str,
    message: str,
    link: Optional[str] = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"message": message[:63206]}
    if link:
        data["link"] = link
    return await graph_post(f"{page_id}/feed", page_access_token, data=data)


def set_default_page(db: Session, account: SocialAccount, page_id: str, page_name: str) -> SocialAccount:
    metadata = get_account_metadata(account)
    metadata["default_page_id"] = page_id
    metadata["default_page_name"] = page_name
    pages = metadata.get("pages") or []
    match = next((p for p in pages if str(p.get("id")) == page_id), None)
    if match and match.get("access_token"):
        metadata["default_page_access_token"] = match["access_token"]
    return update_social_account_metadata(db, account, metadata)
