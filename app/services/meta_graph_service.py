"""Shared Meta (Facebook / Instagram) Graph API OAuth helpers."""

import logging
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.services.http_ssl import httpx_verify
from app.services.social_platform_app_service import OAuthAppCredentials

logger = logging.getLogger(__name__)

GRAPH_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
META_AUTH_URL = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"


def build_meta_authorization_url(
    credentials: OAuthAppCredentials,
    state: str,
    *,
    scopes: str,
) -> str:
    params = {
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "state": state,
        "scope": scopes,
        "response_type": "code",
    }
    return f"{META_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_user_token(
    credentials: OAuthAppCredentials,
    code: str,
) -> dict[str, Any]:
    params = {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "redirect_uri": credentials.redirect_uri,
        "code": code,
    }
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(f"{GRAPH_BASE}/oauth/access_token", params=params)
    if response.status_code >= 400:
        logger.error("Meta code exchange failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def exchange_long_lived_user_token(
    credentials: OAuthAppCredentials,
    short_lived_token: str,
) -> dict[str, Any]:
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "fb_exchange_token": short_lived_token,
    }
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(f"{GRAPH_BASE}/oauth/access_token", params=params)
    if response.status_code >= 400:
        logger.error("Meta long-lived token exchange failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def graph_get(
    path: str,
    access_token: str,
    *,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    query = dict(params or {})
    query["access_token"] = access_token
    url = path if path.startswith("http") else f"{GRAPH_BASE}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(url, params=query)
    if response.status_code >= 400:
        logger.error("Meta Graph GET %s failed: %s", path, response.text)
        response.raise_for_status()
    return response.json()


async def graph_post(
    path: str,
    access_token: str,
    *,
    data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload = dict(data or {})
    payload["access_token"] = access_token
    url = path if path.startswith("http") else f"{GRAPH_BASE}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.post(url, data=payload)
    if response.status_code >= 400:
        logger.error("Meta Graph POST %s failed: %s", path, response.text)
        response.raise_for_status()
    return response.json()


async def fetch_user_pages(user_access_token: str) -> list[dict[str, Any]]:
    payload = await graph_get(
        "me/accounts",
        user_access_token,
        params={"fields": "id,name,access_token,instagram_business_account"},
    )
    pages: list[dict[str, Any]] = []
    for item in payload.get("data", []):
        page_id = item.get("id")
        if not page_id:
            continue
        ig_account = item.get("instagram_business_account") or {}
        pages.append(
            {
                "id": str(page_id),
                "name": str(item.get("name") or "Page"),
                "access_token": str(item.get("access_token") or ""),
                "instagram_business_account_id": (
                    str(ig_account.get("id")) if ig_account.get("id") else None
                ),
            }
        )
    return pages


async def fetch_instagram_username(ig_user_id: str, page_access_token: str) -> str:
    payload = await graph_get(
        ig_user_id,
        page_access_token,
        params={"fields": "username"},
    )
    return str(payload.get("username") or ig_user_id)
