import json
import logging
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.services.http_ssl import httpx_verify
from app.services.social_account_service import (
    get_account_metadata,
    get_decrypted_access_token,
    get_user_social_account,
    upsert_social_account_tokens,
)

logger = logging.getLogger(__name__)

# gql.hashnode.com historically 301'd to an HTML page; gql-beta is the live GraphQL host.
HASHNODE_GQL_URL = "https://gql-beta.hashnode.com"

ME_QUERY = """
query Me {
  me {
    id
    name
    username
    publications(first: 10) {
      edges {
        node {
          id
          title
        }
      }
    }
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


def _auth_header(token: str) -> str:
    """Hashnode accepts the PAT with or without a Bearer prefix."""
    value = (token or "").strip()
    if value.lower().startswith("bearer "):
        return value
    return f"Bearer {value}"


def _first_publication_id(profile: dict[str, Any]) -> Optional[str]:
    pubs = profile.get("publications") or {}
    edges = pubs.get("edges") if isinstance(pubs, dict) else None
    if not isinstance(edges, list):
        return None
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        node = edge.get("node") or {}
        pub_id = node.get("id") if isinstance(node, dict) else None
        if pub_id:
            return str(pub_id)
    return None


async def _graphql(token: str, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify(), follow_redirects=True) as client:
        response = await client.post(
            HASHNODE_GQL_URL,
            headers={
                "Authorization": _auth_header(token),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"query": query, "variables": variables or {}},
        )
    if response.status_code >= 400:
        raw = (response.text or "").strip()[:600]
        logger.error("Hashnode GraphQL HTTP error %s: %s", response.status_code, raw)
        raise ValueError(
            f"Hashnode API error (HTTP {response.status_code}): "
            f"{raw or 'empty response'}. "
            "Check that your personal access token is valid and the publication is on a Pro plan."
        )
    content_type = (response.headers.get("content-type") or "").lower()
    raw = (response.text or "").strip()
    if not raw or "application/json" not in content_type:
        raise ValueError(
            "Hashnode returned a non-JSON response. "
            "Use a Personal Access Token from hashnode.com/settings/developer."
        )
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Hashnode returned invalid JSON. "
            "Use a Personal Access Token from hashnode.com/settings/developer."
        ) from exc
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
    if not publication_id:
        raise ValueError(
            "Hashnode publication ID is required. Reconnect Hashnode in Social accounts "
            "so your blog publication can be detected, or ensure the account has a publication."
        )

    input_payload: dict[str, Any] = {
        "title": title[:256],
        "contentMarkdown": content_markdown,
        "publicationId": publication_id,
        "tags": tags or [],
    }

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
    publication_id = _first_publication_id(profile)
    if not publication_id:
        raise ValueError(
            "No Hashnode publication found on this account. "
            "Create a blog on Hashnode first, then reconnect."
        )

    return upsert_social_account_tokens(
        db,
        user_id,
        "hashnode",
        access_token=api_token.strip(),
        refresh_token=None,
        token_expires_at=None,
        account_label=label,
        metadata={
            "username": username,
            "profile": profile,
            "publication_id": publication_id,
        },
    )


def get_hashnode_token(db: Session, user_id: int) -> str:
    account = get_user_social_account(db, user_id, "hashnode")
    if not account:
        raise ValueError("Hashnode account not connected")
    return get_decrypted_access_token(account)


def get_hashnode_publication_id(db: Session, user_id: int) -> Optional[str]:
    account = get_user_social_account(db, user_id, "hashnode")
    if not account:
        return None
    metadata = get_account_metadata(account)
    pub_id = metadata.get("publication_id")
    if pub_id:
        return str(pub_id)
    profile = metadata.get("profile")
    if isinstance(profile, dict):
        return _first_publication_id(profile)
    return None
