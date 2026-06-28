import base64
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
    update_social_account_metadata,
    upsert_social_account_tokens,
)
from app.services.social_platform_app_service import (
    PinterestAppCredentials,
    get_pinterest_app_credentials,
)

logger = logging.getLogger(__name__)

PINTEREST_AUTH_URL = "https://www.pinterest.com/oauth/"
PINTEREST_TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"
PINTEREST_API_BASE = "https://api.pinterest.com/v5"

PINTEREST_SCOPES = (
    "boards:read,boards:read_secret,boards:write,"
    "pins:read,pins:write,user_accounts:read"
)


def build_authorization_url(credentials: PinterestAppCredentials, state: str) -> str:
    params = {
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "response_type": "code",
        "scope": PINTEREST_SCOPES,
        "state": state,
    }
    return f"{PINTEREST_AUTH_URL}?{urlencode(params)}"


def _basic_auth_header(credentials: PinterestAppCredentials) -> str:
    raw = f"{credentials.client_id}:{credentials.client_secret}"
    return base64.b64encode(raw.encode()).decode()


async def _exchange_token(
    credentials: PinterestAppCredentials,
    data: dict[str, str],
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            PINTEREST_TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {_basic_auth_header(credentials)}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    if response.status_code >= 400:
        logger.error("Pinterest token exchange failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def exchange_code_for_tokens(
    credentials: PinterestAppCredentials,
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
    credentials: PinterestAppCredentials,
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


async def _api_request(
    method: str,
    path: str,
    access_token: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    url = f"{PINTEREST_API_BASE}{path}"
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.request(
            method,
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json=json_body,
            params=params,
        )
    if response.status_code >= 400:
        logger.error("Pinterest API %s %s failed: %s", method, path, response.text)
        response.raise_for_status()
    if response.content:
        return response.json()
    return {}


async def fetch_user_account(access_token: str) -> dict[str, Any]:
    return await _api_request("GET", "/user_account", access_token)


async def fetch_boards(
    access_token: str,
    page_size: int = 25,
    *,
    create_if_empty: bool = False,
) -> list[dict[str, Any]]:
    """List boards (public + secret). Optionally create a default public board."""
    items: list[dict[str, Any]] = []
    bookmark: str | None = None
    while True:
        params: dict[str, Any] = {"page_size": page_size, "privacy": "PUBLIC_AND_SECRET"}
        if bookmark:
            params["bookmark"] = bookmark
        payload = await _api_request("GET", "/boards", access_token, params=params)
        page_items = payload.get("items")
        if isinstance(page_items, list):
            items.extend(page_items)
        bookmark = payload.get("bookmark")
        if not bookmark:
            break

    if items or not create_if_empty:
        return items

    try:
        created = await _api_request(
            "POST",
            "/boards",
            access_token,
            json_body={"name": "Social Posts", "privacy": "PUBLIC"},
        )
        if created.get("id"):
            return [created]
    except Exception as exc:
        logger.warning("Pinterest create default board failed: %s", exc)
    return []


async def create_pin(
    access_token: str,
    *,
    board_id: str,
    title: str,
    description: Optional[str],
    link: Optional[str],
    image_url: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "board_id": board_id,
        "title": title[:100],
        "media_source": {
            "source_type": "image_url",
            "url": image_url,
        },
    }
    if description:
        body["description"] = description[:500]
    if link:
        body["link"] = link
    return await _api_request("POST", "/pins", access_token, json_body=body)


async def save_pinterest_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_pinterest_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("Pinterest app credentials not found. Connect again from Social accounts.")

    token_payload = await exchange_code_for_tokens(credentials, code)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    expires_at = _expires_at_from_token_payload(token_payload)

    profile = await fetch_user_account(access_token)
    username = profile.get("username") or profile.get("id") or "Pinterest"
    boards = await fetch_boards(access_token, create_if_empty=True)
    default_board = boards[0] if boards else None

    metadata: dict[str, Any] = {
        "username": username,
        "profile": profile,
        "boards": [
            {"id": b.get("id"), "name": b.get("name")}
            for b in boards
            if b.get("id")
        ],
    }
    if default_board and default_board.get("id"):
        metadata["default_board_id"] = default_board["id"]
        metadata["default_board_name"] = default_board.get("name")

    return upsert_social_account_tokens(
        db,
        user_id,
        "pinterest",
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        account_label=f"@{username}" if not str(username).startswith("@") else str(username),
        metadata=metadata,
    )


async def get_valid_pinterest_access_token(db: Session, user_id: int) -> str:
    account = get_user_social_account(db, user_id, "pinterest")
    if not account:
        raise ValueError("Pinterest account not connected")

    credentials = get_pinterest_app_credentials(db, user_id)
    if not credentials:
        raise ValueError("Pinterest app credentials missing")

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
                    "pinterest",
                    access_token=token_payload["access_token"],
                    refresh_token=token_payload.get("refresh_token") or refresh_token,
                    token_expires_at=_expires_at_from_token_payload(token_payload),
                    account_label=account.account_label,
                    metadata=get_account_metadata(account),
                )
                return token_payload["access_token"]

    return get_decrypted_access_token(account)


def set_default_board(
    db: Session,
    account: SocialAccount,
    board_id: str,
    board_name: Optional[str] = None,
) -> SocialAccount:
    metadata = get_account_metadata(account)
    metadata["default_board_id"] = board_id
    if board_name:
        metadata["default_board_name"] = board_name
    return update_social_account_metadata(db, account, metadata)


def _board_id_from_metadata(
    metadata: dict[str, Any],
    platform_options: dict[str, Any],
) -> str | None:
    raw = platform_options.get("board_id") or metadata.get("default_board_id")
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    boards = metadata.get("boards")
    if isinstance(boards, list):
        for entry in boards:
            if isinstance(entry, dict) and entry.get("id") is not None:
                return str(entry["id"])
    return None


async def resolve_pinterest_board_id(
    db: Session,
    user_id: int,
    account: SocialAccount,
    platform_options: dict[str, Any] | None = None,
) -> str | None:
    """Resolve target board: explicit option → saved default → cached boards → live API fetch."""
    platform_options = platform_options or {}
    metadata = get_account_metadata(account)
    board_id = _board_id_from_metadata(metadata, platform_options)
    if board_id:
        return board_id

    access_token = await get_valid_pinterest_access_token(db, user_id)
    boards_raw = await fetch_boards(access_token, create_if_empty=True)
    if not boards_raw:
        return None
    first = boards_raw[0]
    bid = first.get("id")
    if not bid:
        return None
    resolved = str(bid)
    set_default_board(db, account, resolved, str(first.get("name") or "Board"))
    return resolved

