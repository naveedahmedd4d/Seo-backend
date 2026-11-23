"""Tumblr OAuth 1.0a + text-post publishing for backlink outreach."""

from __future__ import annotations

import binascii
import hashlib
import hmac
import logging
import secrets
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, urlencode

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
    OAuthAppCredentials,
    get_tumblr_app_credentials,
)

logger = logging.getLogger(__name__)

TUMBLR_REQUEST_TOKEN_URL = "https://www.tumblr.com/oauth/request_token"
TUMBLR_AUTHORIZE_URL = "https://www.tumblr.com/oauth/authorize"
TUMBLR_ACCESS_TOKEN_URL = "https://www.tumblr.com/oauth/access_token"
TUMBLR_API_BASE = "https://api.tumblr.com/v2"

# Tumblr OAuth1 does not echo a state param; stash request-token secrets here
# for the short authorize window (single-process / sticky enough for BYO apps).
_pending_request_tokens: dict[str, dict[str, Any]] = {}


def store_pending_request_token(
    oauth_token: str,
    *,
    user_id: int,
    oauth_token_secret: str,
) -> None:
    _pending_request_tokens[oauth_token] = {
        "user_id": user_id,
        "oauth_token_secret": oauth_token_secret,
        "created_at": time.time(),
    }


def pop_pending_request_token(oauth_token: str) -> Optional[dict[str, Any]]:
    pending = _pending_request_tokens.pop(oauth_token, None)
    if not pending:
        return None
    if time.time() - float(pending.get("created_at") or 0) > 600:
        return None
    return pending


def _percent_encode(value: str) -> str:
    return quote(str(value), safe="~")


def _oauth1_signature(
    method: str,
    url: str,
    params: dict[str, str],
    consumer_secret: str,
    token_secret: str = "",
) -> str:
    items = sorted((k, v) for k, v in params.items())
    param_string = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in items
    )
    base = "&".join(
        [
            method.upper(),
            _percent_encode(url),
            _percent_encode(param_string),
        ]
    )
    key = f"{_percent_encode(consumer_secret)}&{_percent_encode(token_secret)}"
    digest = hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    return binascii.b2a_base64(digest).decode().strip()


def _oauth1_header(
    method: str,
    url: str,
    credentials: OAuthAppCredentials,
    *,
    token: Optional[str] = None,
    token_secret: str = "",
    extra_params: Optional[dict[str, str]] = None,
    body_params: Optional[dict[str, str]] = None,
) -> str:
    oauth_params: dict[str, str] = {
        "oauth_consumer_key": credentials.client_id,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
    }
    if token:
        oauth_params["oauth_token"] = token
    if extra_params:
        oauth_params.update(extra_params)

    sign_params = dict(oauth_params)
    if body_params:
        sign_params.update(body_params)

    oauth_params["oauth_signature"] = _oauth1_signature(
        method,
        url,
        sign_params,
        credentials.client_secret,
        token_secret,
    )
    header_value = ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_value}"


async def fetch_request_token(credentials: OAuthAppCredentials) -> dict[str, str]:
    extra = {"oauth_callback": credentials.redirect_uri}
    header = _oauth1_header(
        "POST",
        TUMBLR_REQUEST_TOKEN_URL,
        credentials,
        extra_params=extra,
    )
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            TUMBLR_REQUEST_TOKEN_URL,
            headers={"Authorization": header},
        )
    if response.status_code >= 400:
        logger.error("Tumblr request_token failed: %s", response.text)
        response.raise_for_status()
    data = dict(parse_qsl(response.text))
    if not data.get("oauth_token") or not data.get("oauth_token_secret"):
        raise ValueError("Tumblr did not return a request token")
    return data


def build_authorization_url(oauth_token: str) -> str:
    return f"{TUMBLR_AUTHORIZE_URL}?{urlencode({'oauth_token': oauth_token})}"


async def exchange_verifier_for_tokens(
    credentials: OAuthAppCredentials,
    *,
    oauth_token: str,
    oauth_token_secret: str,
    oauth_verifier: str,
) -> dict[str, str]:
    header = _oauth1_header(
        "POST",
        TUMBLR_ACCESS_TOKEN_URL,
        credentials,
        token=oauth_token,
        token_secret=oauth_token_secret,
        extra_params={"oauth_verifier": oauth_verifier},
    )
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            TUMBLR_ACCESS_TOKEN_URL,
            headers={"Authorization": header},
        )
    if response.status_code >= 400:
        logger.error("Tumblr access_token failed: %s", response.text)
        response.raise_for_status()
    data = dict(parse_qsl(response.text))
    if not data.get("oauth_token") or not data.get("oauth_token_secret"):
        raise ValueError("Tumblr did not return access tokens")
    return data


async def _api_request(
    method: str,
    path: str,
    credentials: OAuthAppCredentials,
    *,
    access_token: str,
    access_token_secret: str,
    data: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    url = f"{TUMBLR_API_BASE}{path}"
    header = _oauth1_header(
        method,
        url,
        credentials,
        token=access_token,
        token_secret=access_token_secret,
        body_params=data if method.upper() == "POST" else None,
    )
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.request(
            method,
            url,
            headers={"Authorization": header},
            data=data,
        )
    if response.status_code >= 400:
        logger.error("Tumblr API %s %s failed: %s", method, path, response.text)
        response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


async def fetch_user_info(
    credentials: OAuthAppCredentials,
    access_token: str,
    access_token_secret: str,
) -> dict[str, Any]:
    payload = await _api_request(
        "GET",
        "/user/info",
        credentials,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )
    return payload.get("response", {}).get("user", {}) if isinstance(payload, dict) else {}


async def create_text_post(
    credentials: OAuthAppCredentials,
    *,
    access_token: str,
    access_token_secret: str,
    blog_identifier: str,
    title: str,
    body_html: str,
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    form: dict[str, str] = {
        "type": "text",
        "title": title,
        "body": body_html,
        "state": "published",
        "format": "html",
    }
    if tags:
        form["tags"] = ",".join(tags[:20])
    payload = await _api_request(
        "POST",
        f"/blog/{blog_identifier}/post",
        credentials,
        access_token=access_token,
        access_token_secret=access_token_secret,
        data=form,
    )
    return payload.get("response", {}) if isinstance(payload, dict) else {}


async def start_tumblr_oauth(db: Session, user_id: int, *, redirect_uri: str) -> dict[str, str]:
    credentials = get_tumblr_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("Tumblr app credentials not configured")
    request_token = await fetch_request_token(credentials)
    return {
        "oauth_token": request_token["oauth_token"],
        "oauth_token_secret": request_token["oauth_token_secret"],
        "authorization_url": build_authorization_url(request_token["oauth_token"]),
    }


async def save_tumblr_account_from_verifier(
    db: Session,
    user_id: int,
    *,
    oauth_token: str,
    oauth_token_secret: str,
    oauth_verifier: str,
    redirect_uri: str,
) -> SocialAccount:
    credentials = get_tumblr_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("Tumblr app credentials not configured")

    tokens = await exchange_verifier_for_tokens(
        credentials,
        oauth_token=oauth_token,
        oauth_token_secret=oauth_token_secret,
        oauth_verifier=oauth_verifier,
    )
    access_token = tokens["oauth_token"]
    access_secret = tokens["oauth_token_secret"]
    user = await fetch_user_info(credentials, access_token, access_secret)
    blogs = user.get("blogs") if isinstance(user.get("blogs"), list) else []
    primary = next((b for b in blogs if b.get("primary")), blogs[0] if blogs else None)
    blog_name = (primary or {}).get("name") or user.get("name") or "Tumblr"
    blog_url = (primary or {}).get("url")
    metadata: dict[str, Any] = {
        "name": user.get("name"),
        "blogs": [
            {
                "name": b.get("name"),
                "title": b.get("title"),
                "url": b.get("url"),
                "primary": bool(b.get("primary")),
            }
            for b in blogs
            if b.get("name")
        ],
    }
    if primary and primary.get("name"):
        metadata["default_blog_name"] = primary["name"]
        metadata["default_blog_url"] = blog_url

    return upsert_social_account_tokens(
        db,
        user_id,
        "tumblr",
        access_token=access_token,
        refresh_token=access_secret,  # OAuth1 token secret stored as refresh_token
        token_expires_at=None,
        account_label=str(blog_name),
        metadata=metadata,
    )


def get_tumblr_token_pair(db: Session, user_id: int) -> tuple[str, str]:
    account = get_user_social_account(db, user_id, "tumblr")
    if not account:
        raise ValueError("Tumblr account not connected")
    return get_decrypted_access_token(account), get_decrypted_refresh_token(account) or ""


def set_default_blog(
    db: Session,
    account: SocialAccount,
    blog_name: str,
    blog_url: Optional[str] = None,
) -> SocialAccount:
    metadata = get_account_metadata(account)
    metadata["default_blog_name"] = blog_name
    if blog_url:
        metadata["default_blog_url"] = blog_url
    return update_social_account_metadata(
        db,
        account,
        metadata,
        account_label=blog_name,
    )
