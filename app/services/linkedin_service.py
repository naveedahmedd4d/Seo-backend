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
from app.services.social_platform_app_service import OAuthAppCredentials, get_linkedin_app_credentials

logger = logging.getLogger(__name__)

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com"
# Marketing / Community Management versioned REST calls.
LINKEDIN_REST_VERSION = "202411"

# w_organization_social: post as a Company Page the member administers.
# rw_organization_admin: list organization ACLs so we can offer a Page picker.
LINKEDIN_SCOPES = (
    "openid profile email w_member_social w_organization_social "
    "rw_organization_admin r_organization_admin"
)

# Wire values from LinkedIn Organization Access Control docs.
_PAGE_POST_ROLES = frozenset(
    {
        "ADMINISTRATOR",
        "CONTENT_ADMIN",
        "CONTENT_ADMINISTRATOR",
        "DIRECT_SPONSORED_CONTENT_POSTER",
        "RECRUITING_POSTER",
    }
)


def build_authorization_url(credentials: OAuthAppCredentials, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "state": state,
        "scope": LINKEDIN_SCOPES,
    }
    return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"


async def _exchange_token(
    credentials: OAuthAppCredentials,
    data: dict[str, str],
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            LINKEDIN_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        logger.error("LinkedIn token exchange failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def exchange_code_for_tokens(
    credentials: OAuthAppCredentials,
    code: str,
) -> dict[str, Any]:
    return await _exchange_token(
        credentials,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": credentials.redirect_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        },
    )


async def refresh_access_token(
    credentials: OAuthAppCredentials,
    refresh_token: str,
) -> dict[str, Any]:
    return await _exchange_token(
        credentials,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        },
    )


def _expires_at_from_token_payload(payload: dict[str, Any]) -> Optional[datetime]:
    expires_in = payload.get("expires_in")
    if not expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


async def fetch_userinfo(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{LINKEDIN_API_BASE}/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        logger.error("LinkedIn userinfo failed: %s", response.text)
        response.raise_for_status()
    return response.json()


def person_urn_from_userinfo(userinfo: dict[str, Any]) -> str:
    sub = userinfo.get("sub")
    if not sub:
        raise ValueError("LinkedIn profile missing subject id")
    sub_str = str(sub)
    if sub_str.startswith("urn:li:"):
        return sub_str
    return f"urn:li:person:{sub_str}"


def _rest_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": LINKEDIN_REST_VERSION,
    }


def organization_urn_from_id(organization_id: str) -> str:
    org_id = (organization_id or "").strip()
    if not org_id:
        raise ValueError("LinkedIn organization id is required")
    if org_id.startswith("urn:li:organization:"):
        return org_id
    if org_id.startswith("urn:li:"):
        return org_id
    return f"urn:li:organization:{org_id}"


def organization_id_from_urn(urn: str) -> str:
    value = (urn or "").strip()
    if value.startswith("urn:li:organization:"):
        return value.rsplit(":", 1)[-1]
    return value


async def fetch_administered_organizations(
    access_token: str,
) -> tuple[list[dict[str, str]], Optional[str]]:
    """Return (Company Pages the member can post as, optional discovery warning)."""
    by_id: dict[str, dict[str, str]] = {}
    warnings: list[str] = []

    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        # Prefer unfiltered roleAssignee (LinkedIn docs); then fall back to explicit roles.
        query_variants: list[dict[str, str | int]] = [
            {"q": "roleAssignee", "state": "APPROVED", "count": 100},
            {"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED", "count": 100},
            {
                "q": "roleAssignee",
                "role": "CONTENT_ADMINISTRATOR",
                "state": "APPROVED",
                "count": 100,
            },
            {
                "q": "roleAssignee",
                "role": "DIRECT_SPONSORED_CONTENT_POSTER",
                "state": "APPROVED",
                "count": 100,
            },
        ]
        endpoints = (
            f"{LINKEDIN_API_BASE}/rest/organizationAcls",
            f"{LINKEDIN_API_BASE}/v2/organizationAcls",
        )

        for endpoint in endpoints:
            for params in query_variants:
                headers = (
                    _rest_headers(access_token)
                    if "/rest/" in endpoint
                    else {
                        "Authorization": f"Bearer {access_token}",
                        "X-Restli-Protocol-Version": "2.0.0",
                    }
                )
                response = await client.get(endpoint, params=params, headers=headers)
                if response.status_code == 403:
                    warnings.append(
                        "LinkedIn denied Page listing (need Community Management API + "
                        "rw_organization_admin). Reconnect after enabling the product."
                    )
                    logger.warning(
                        "LinkedIn organizationAcls forbidden at %s params=%s: %s",
                        endpoint,
                        params,
                        response.text[:300],
                    )
                    continue
                if response.status_code >= 400:
                    logger.error(
                        "LinkedIn organizationAcls failed at %s params=%s: %s",
                        endpoint,
                        params,
                        response.text[:500],
                    )
                    continue
                payload = response.json() if response.content else {}
                for element in payload.get("elements") or []:
                    if not isinstance(element, dict):
                        continue
                    state = str(element.get("state") or "APPROVED").upper()
                    if state and state != "APPROVED":
                        continue
                    element_role = str(element.get("role") or "").upper()
                    if element_role and element_role not in _PAGE_POST_ROLES:
                        continue
                    org_urn = str(element.get("organization") or "").strip()
                    if not org_urn:
                        continue
                    org_id = organization_id_from_urn(org_urn)
                    if not org_id or org_id in by_id:
                        continue
                    name = await _fetch_organization_name(client, access_token, org_id)
                    by_id[org_id] = {
                        "id": org_id,
                        "name": name or f"Page {org_id}",
                        "urn": org_urn,
                    }
            if by_id:
                break

    warning = warnings[0] if warnings and not by_id else None
    return sorted(by_id.values(), key=lambda item: item["name"].lower()), warning


async def resolve_organization_by_vanity_or_id(
    access_token: str,
    *,
    vanity_name: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> dict[str, str]:
    """Resolve a Company Page by vanity slug (e.g. webkitsolutions) or numeric id."""
    vanity = (vanity_name or "").strip().strip("/")
    if vanity.lower().startswith("https://"):
        # Accept full LinkedIn company URLs.
        parts = vanity.rstrip("/").split("/")
        if "company" in parts:
            idx = parts.index("company")
            if idx + 1 < len(parts):
                vanity = parts[idx + 1]
    if vanity.lower().startswith("linkedin.com"):
        parts = vanity.split("/")
        if "company" in parts:
            idx = parts.index("company")
            if idx + 1 < len(parts):
                vanity = parts[idx + 1]

    org_id = organization_id_from_urn((organization_id or "").strip()) if organization_id else ""

    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        if not org_id and vanity:
            for endpoint, headers in (
                (
                    f"{LINKEDIN_API_BASE}/rest/organizations",
                    _rest_headers(access_token),
                ),
                (
                    f"{LINKEDIN_API_BASE}/v2/organizations",
                    {
                        "Authorization": f"Bearer {access_token}",
                        "X-Restli-Protocol-Version": "2.0.0",
                    },
                ),
            ):
                response = await client.get(
                    endpoint,
                    params={"q": "vanityName", "vanityName": vanity},
                    headers=headers,
                )
                if response.status_code >= 400:
                    logger.warning(
                        "LinkedIn vanity lookup failed for %s via %s: %s",
                        vanity,
                        endpoint,
                        response.text[:400],
                    )
                    continue
                payload = response.json() if response.content else {}
                elements = payload.get("elements")
                if isinstance(elements, list) and elements:
                    first = elements[0]
                    if isinstance(first, dict):
                        raw_id = first.get("id") or first.get("organization")
                        if raw_id is not None:
                            org_id = organization_id_from_urn(str(raw_id))
                        name = first.get("localizedName")
                        if not isinstance(name, str) or not name.strip():
                            name_obj = first.get("name")
                            if isinstance(name_obj, dict):
                                localized = name_obj.get("localized")
                                if isinstance(localized, dict):
                                    for value in localized.values():
                                        if isinstance(value, str) and value.strip():
                                            name = value
                                            break
                        if org_id:
                            return {
                                "id": org_id,
                                "name": (name.strip() if isinstance(name, str) else None)
                                or vanity
                                or org_id,
                                "urn": organization_urn_from_id(org_id),
                            }

        if not org_id:
            raise ValueError(
                "Could not find that LinkedIn Company Page. Use the company slug from the "
                "page URL (linkedin.com/company/your-slug) or the numeric organization id."
            )

        name = await _fetch_organization_name(client, access_token, org_id)
        return {
            "id": org_id,
            "name": name or vanity or f"Page {org_id}",
            "urn": organization_urn_from_id(org_id),
        }


async def _fetch_organization_name(
    client: httpx.AsyncClient,
    access_token: str,
    organization_id: str,
) -> Optional[str]:
    org_id = organization_id_from_urn(organization_id)
    response = await client.get(
        f"{LINKEDIN_API_BASE}/rest/organizations/{org_id}",
        headers=_rest_headers(access_token),
    )
    if response.status_code >= 400:
        # Fallback for apps still on the classic v2 Organizations API.
        response = await client.get(
            f"{LINKEDIN_API_BASE}/v2/organizations/{org_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        if response.status_code >= 400:
            logger.warning(
                "LinkedIn organization lookup failed for %s: %s",
                org_id,
                response.text,
            )
            return None
    data = response.json() if response.content else {}
    localized = data.get("localizedName")
    if isinstance(localized, str) and localized.strip():
        return localized.strip()
    name_obj = data.get("name")
    if isinstance(name_obj, dict):
        preferred = name_obj.get("localized")
        if isinstance(preferred, dict):
            for value in preferred.values():
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


async def create_text_share(
    access_token: str,
    *,
    author_urn: str,
    text: str,
    article_url: Optional[str] = None,
) -> dict[str, Any]:
    """Publish a text (optional article link) share as a member or organization."""
    author = (author_urn or "").strip()
    if not author:
        raise ValueError("LinkedIn author URN is required")

    # Company Pages use the versioned Posts API; personal profiles keep ugcPosts.
    if author.startswith("urn:li:organization:"):
        return await _create_organization_post(
            access_token,
            organization_urn=author,
            text=text,
            article_url=article_url,
        )

    share_content: dict[str, Any] = {
        "shareCommentary": {"text": text[:3000]},
        "shareMediaCategory": "ARTICLE" if article_url else "NONE",
    }
    if article_url:
        share_content["media"] = [
            {
                "status": "READY",
                "originalUrl": article_url,
            }
        ]

    body = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": share_content},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{LINKEDIN_API_BASE}/v2/ugcPosts",
            json=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
    if response.status_code >= 400:
        logger.error("LinkedIn ugcPosts failed: %s", response.text)
        response.raise_for_status()
    if response.status_code == 201:
        post_id = response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id")
        return {"id": post_id}
    return response.json() if response.content else {}


async def _create_organization_post(
    access_token: str,
    *,
    organization_urn: str,
    text: str,
    article_url: Optional[str] = None,
) -> dict[str, Any]:
    commentary = (text or "").strip()
    if article_url:
        commentary = f"{commentary}\n\n{article_url}".strip() if commentary else article_url

    body: dict[str, Any] = {
        "author": organization_urn,
        "commentary": commentary[:3000],
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{LINKEDIN_API_BASE}/rest/posts",
            json=body,
            headers=_rest_headers(access_token),
        )
    if response.status_code >= 400:
        logger.error("LinkedIn organization posts failed: %s", response.text)
        response.raise_for_status()
    post_id = response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id")
    if post_id:
        return {"id": post_id}
    return response.json() if response.content else {}


async def save_linkedin_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_linkedin_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("LinkedIn app credentials not found. Connect again from Social accounts.")

    token_payload = await exchange_code_for_tokens(credentials, code)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    expires_at = _expires_at_from_token_payload(token_payload)

    userinfo = await fetch_userinfo(access_token)
    person_urn = person_urn_from_userinfo(userinfo)
    name = userinfo.get("name") or userinfo.get("email") or "LinkedIn"

    organizations: list[dict[str, str]] = []
    discovery_warning: Optional[str] = None
    try:
        organizations, discovery_warning = await fetch_administered_organizations(access_token)
    except Exception:
        logger.exception("LinkedIn organization discovery failed during connect")
        discovery_warning = "Could not load Company Pages during connect. Try Refresh or add by slug."

    metadata: dict[str, Any] = {
        "person_urn": person_urn,
        "sub": userinfo.get("sub"),
        "name": name,
        "userinfo": userinfo,
        "organizations": organizations,
        "organizations_warning": discovery_warning,
        # Keep both destinations available; default to personal until the user picks a Page.
        "default_organization_id": None,
        "default_organization_name": None,
        "post_as": "person",
    }

    return upsert_social_account_tokens(
        db,
        user_id,
        "linkedin",
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        account_label=str(name),
        metadata=metadata,
    )


def set_default_linkedin_organization(
    db: Session,
    account: SocialAccount,
    *,
    organization_id: Optional[str],
) -> SocialAccount:
    """Set Company Page target, or None/empty to post as the personal profile."""
    metadata = get_account_metadata(account)
    orgs = metadata.get("organizations") or []
    if not isinstance(orgs, list):
        orgs = []

    if not organization_id or organization_id in ("person", "personal", "__person__"):
        metadata["post_as"] = "person"
        metadata["default_organization_id"] = None
        metadata["default_organization_name"] = None
        person_name = metadata.get("name") or account.account_label or "LinkedIn"
        account.account_label = str(person_name)
        return update_social_account_metadata(db, account, metadata)

    org_id = organization_id_from_urn(organization_id)
    match = next(
        (o for o in orgs if isinstance(o, dict) and str(o.get("id")) == org_id),
        None,
    )
    if not match:
        raise ValueError("LinkedIn Company Page not found on this account")

    metadata["post_as"] = "organization"
    metadata["default_organization_id"] = org_id
    metadata["default_organization_name"] = match.get("name") or org_id
    account.account_label = str(match.get("name") or org_id)
    return update_social_account_metadata(db, account, metadata)


async def refresh_linkedin_organizations(
    db: Session, user_id: int
) -> tuple[list[dict[str, str]], Optional[str]]:
    access_token, _author = await get_valid_linkedin_access_token(db, user_id, prefer_person=True)
    organizations, warning = await fetch_administered_organizations(access_token)
    account = get_user_social_account(db, user_id, "linkedin")
    if not account:
        raise ValueError("LinkedIn account not connected")
    metadata = get_account_metadata(account)
    # Preserve manually added Pages that ACL discovery may not return yet.
    existing = metadata.get("organizations") or []
    if isinstance(existing, list):
        known_ids = {str(o.get("id")) for o in organizations if isinstance(o, dict)}
        for entry in existing:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            if str(entry["id"]) in known_ids:
                continue
            if entry.get("manual"):
                organizations.append(
                    {
                        "id": str(entry["id"]),
                        "name": str(entry.get("name") or entry["id"]),
                        "urn": str(entry.get("urn") or organization_urn_from_id(str(entry["id"]))),
                        "manual": True,
                    }
                )
    metadata["organizations"] = organizations
    metadata["organizations_warning"] = warning
    default_id = metadata.get("default_organization_id")
    if default_id and not any(str(o.get("id")) == str(default_id) for o in organizations):
        # Previously selected Page is gone — fall back to personal profile.
        metadata["default_organization_id"] = None
        metadata["default_organization_name"] = None
        metadata["post_as"] = "person"
    update_social_account_metadata(db, account, metadata)
    return organizations, warning


async def add_linkedin_organization(
    db: Session,
    user_id: int,
    *,
    vanity_name: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> tuple[SocialAccount, dict[str, str]]:
    """Resolve a Company Page by slug/id and store it on the LinkedIn account."""
    account = get_user_social_account(db, user_id, "linkedin")
    if not account:
        raise ValueError("LinkedIn account not connected")
    access_token, _author = await get_valid_linkedin_access_token(db, user_id, prefer_person=True)
    org = await resolve_organization_by_vanity_or_id(
        access_token,
        vanity_name=vanity_name,
        organization_id=organization_id,
    )
    org["manual"] = True
    metadata = get_account_metadata(account)
    orgs = metadata.get("organizations") or []
    if not isinstance(orgs, list):
        orgs = []
    next_orgs: list[dict[str, Any]] = [
        o for o in orgs if isinstance(o, dict) and str(o.get("id")) != org["id"]
    ]
    next_orgs.append(org)
    metadata["organizations"] = next_orgs
    metadata["organizations_warning"] = None
    metadata["post_as"] = "organization"
    metadata["default_organization_id"] = org["id"]
    metadata["default_organization_name"] = org["name"]
    account.account_label = org["name"]
    updated = update_social_account_metadata(db, account, metadata)
    return updated, org


def resolve_linkedin_author_urn(
    metadata: dict[str, Any],
    *,
    organization_id: Optional[str] = None,
    prefer_person: bool = False,
) -> str:
    if prefer_person:
        person_urn = str(metadata.get("person_urn") or "")
        if not person_urn:
            raise ValueError("LinkedIn profile metadata missing")
        return person_urn

    override = (organization_id or "").strip()
    if override in ("person", "personal", "__person__"):
        person_urn = str(metadata.get("person_urn") or "")
        if not person_urn:
            raise ValueError("LinkedIn profile metadata missing")
        return person_urn
    if override:
        return organization_urn_from_id(override)

    post_as = str(metadata.get("post_as") or "").strip().lower()
    default_org = metadata.get("default_organization_id")
    if post_as == "organization" and default_org:
        return organization_urn_from_id(str(default_org))
    if post_as != "person" and default_org:
        return organization_urn_from_id(str(default_org))

    person_urn = str(metadata.get("person_urn") or "")
    if not person_urn:
        raise ValueError("LinkedIn profile metadata missing")
    return person_urn


async def get_valid_linkedin_access_token(
    db: Session,
    user_id: int,
    *,
    organization_id: Optional[str] = None,
    prefer_person: bool = False,
) -> tuple[str, str]:
    account = get_user_social_account(db, user_id, "linkedin")
    if not account:
        raise ValueError("LinkedIn account not connected")

    credentials = get_linkedin_app_credentials(db, user_id)
    if not credentials:
        raise ValueError("LinkedIn app credentials missing")

    metadata = get_account_metadata(account)
    author_urn = resolve_linkedin_author_urn(
        metadata,
        organization_id=organization_id,
        prefer_person=prefer_person,
    )

    if account.token_expires_at:
        expires_at = account.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5):
            refresh_token = get_decrypted_refresh_token(account)
            if refresh_token:
                token_payload = await refresh_access_token(credentials, refresh_token)
                upsert_social_account_tokens(
                    db,
                    user_id,
                    "linkedin",
                    access_token=token_payload["access_token"],
                    refresh_token=token_payload.get("refresh_token") or refresh_token,
                    token_expires_at=_expires_at_from_token_payload(token_payload),
                    account_label=account.account_label,
                    metadata=metadata,
                )
                return token_payload["access_token"], author_urn

    return get_decrypted_access_token(account), author_urn
