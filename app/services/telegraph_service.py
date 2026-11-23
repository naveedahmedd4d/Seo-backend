"""Telegra.ph publishing for backlink outreach."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.services.http_ssl import httpx_verify
from app.services.social_account_service import (
    get_decrypted_access_token,
    get_user_social_account,
    upsert_social_account_tokens,
)

logger = logging.getLogger(__name__)

TELEGRAPH_API_BASE = "https://api.telegra.ph"


async def create_account(
    *,
    short_name: str,
    author_name: Optional[str] = None,
    author_url: Optional[str] = None,
) -> dict[str, Any]:
    params: dict[str, str] = {"short_name": short_name[:32]}
    if author_name:
        params["author_name"] = author_name[:128]
    if author_url:
        params["author_url"] = author_url[:512]
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(f"{TELEGRAPH_API_BASE}/createAccount", data=params)
    payload = response.json()
    if response.status_code >= 400 or not payload.get("ok"):
        logger.error("Telegra.ph createAccount failed: %s", response.text)
        raise ValueError(payload.get("error") or "Failed to create Telegra.ph account")
    return payload["result"]


def _paragraph_nodes(text: str) -> list[dict[str, Any] | str]:
    """Convert plain/markdown-ish body into Telegra.ph Node elements."""
    nodes: list[dict[str, Any] | str] = []
    chunks = re.split(r"\n\s*\n", (text or "").strip())
    for chunk in chunks:
        paragraph = chunk.strip()
        if not paragraph:
            continue
        # Strip a leading markdown heading marker for display
        paragraph = re.sub(r"^#{1,6}\s*", "", paragraph)
        # Simple markdown links: [label](url)
        children: list[Any] = []
        last = 0
        for match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", paragraph):
            if match.start() > last:
                children.append(paragraph[last : match.start()])
            children.append(
                {
                    "tag": "a",
                    "attrs": {"href": match.group(2)},
                    "children": [match.group(1)],
                }
            )
            last = match.end()
        if last < len(paragraph):
            children.append(paragraph[last:])
        if not children:
            children = [paragraph]
        nodes.append({"tag": "p", "children": children})
    return nodes


async def create_page(
    access_token: str,
    *,
    title: str,
    content_text: str,
    author_name: Optional[str] = None,
    author_url: Optional[str] = None,
) -> dict[str, Any]:
    content = _paragraph_nodes(content_text)
    if not content:
        content = [{"tag": "p", "children": [title or "Untitled"]}]
    form: dict[str, str] = {
        "access_token": access_token,
        "title": (title or "Untitled")[:256],
        "content": json.dumps(content),
        "return_content": "false",
    }
    if author_name:
        form["author_name"] = author_name[:128]
    if author_url:
        form["author_url"] = author_url[:512]

    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.post(f"{TELEGRAPH_API_BASE}/createPage", data=form)
    result = response.json()
    if response.status_code >= 400 or not result.get("ok"):
        logger.error("Telegra.ph createPage failed: %s", response.text)
        raise ValueError(result.get("error") or "Failed to create Telegra.ph page")
    return result["result"]


async def save_telegraph_account(
    db: Session,
    user_id: int,
    *,
    short_name: str,
    author_name: Optional[str] = None,
    author_url: Optional[str] = None,
) -> SocialAccount:
    profile = await create_account(
        short_name=short_name.strip() or "SeoAutomation",
        author_name=(author_name or "").strip() or None,
        author_url=(author_url or "").strip() or None,
    )
    access_token = profile.get("access_token")
    if not access_token:
        raise ValueError("Telegra.ph did not return an access token")
    label = profile.get("author_name") or profile.get("short_name") or "Telegra.ph"
    return upsert_social_account_tokens(
        db,
        user_id,
        "telegraph",
        access_token=str(access_token),
        refresh_token=None,
        token_expires_at=None,
        account_label=str(label),
        metadata={
            "short_name": profile.get("short_name"),
            "author_name": profile.get("author_name"),
            "author_url": profile.get("author_url"),
            "auth_url": profile.get("auth_url"),
        },
    )


def get_telegraph_access_token(db: Session, user_id: int) -> str:
    account = get_user_social_account(db, user_id, "telegraph")
    if not account:
        raise ValueError("Telegra.ph account not connected")
    return get_decrypted_access_token(account)
