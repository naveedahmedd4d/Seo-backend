import logging
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

HASHNODE_GQL_URL = "https://gql.hashnode.com"

ME_QUERY = """
query Me {
  me {
    id
    name
    username
  }
}
"""

PUBLISH_MUTATION = """
mutation PublishPost($input: PublishPostInput!) {
  publishPost(input: $input) {
    post {
      id
      url
      title
    }
  }
}
"""


async def _graphql(token: str, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.post(
            HASHNODE_GQL_URL,
            headers={"Authorization": token.strip(), "Content-Type": "application/json"},
            json={"query": query, "variables": variables or {}},
        )
    if response.status_code >= 400:
        logger.error("Hashnode GraphQL HTTP error: %s", response.text)
        response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        message = payload["errors"][0].get("message", "Hashnode API error")
        raise ValueError(message)
    return payload.get("data") or {}


async def fetch_user(token: str) -> dict[str, Any]:
    data = await _graphql(token, ME_QUERY)
    me = data.get("me")
    if not me:
        raise ValueError("Invalid Hashnode token")
    return me


async def publish_article(
    token: str,
    *,
    title: str,
    content_markdown: str,
    publication_id: Optional[str] = None,
    tags: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    input_payload: dict[str, Any] = {
        "title": title[:256],
        "contentMarkdown": content_markdown,
        "tags": tags or [],
    }
    if publication_id:
        input_payload["publicationId"] = publication_id

    data = await _graphql(token, PUBLISH_MUTATION, {"input": input_payload})
    post = (data.get("publishPost") or {}).get("post") or {}
    if not post.get("id"):
        raise ValueError("Hashnode did not return a published post")
    return post


async def save_hashnode_account_from_token(
    db: Session,
    user_id: int,
    api_token: str,
) -> SocialAccount:
    profile = await fetch_user(api_token)
    username = profile.get("username") or profile.get("name") or "Hashnode"
    label = f"@{username}" if username and not str(username).startswith("@") else str(username)

    return upsert_social_account_tokens(
        db,
        user_id,
        "hashnode",
        access_token=api_token.strip(),
        refresh_token=None,
        token_expires_at=None,
        account_label=label,
        metadata={"username": username, "profile": profile},
    )


def get_hashnode_token(db: Session, user_id: int) -> str:
    account = get_user_social_account(db, user_id, "hashnode")
    if not account:
        raise ValueError("Hashnode account not connected")
    return get_decrypted_access_token(account)
