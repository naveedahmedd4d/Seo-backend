import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.models.gsc_oauth_token import GscOAuthToken
from app.services.http_ssl import httpx_verify, with_ssl_retry
from app.services.social_platform_app_service import OAuthAppCredentials
from app.services.wordpress_service import decrypt_api_key, encrypt_api_key

logger = logging.getLogger(__name__)

GSC_SCOPE = "https://www.googleapis.com/auth/webmasters"
GSC_SCOPE_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
# Analytics/issue data is read-only in this app (no sitemap-submit or other GSC
# write call exists anywhere in the codebase) — request the minimal scope, plus
# openid/email so Google returns an id_token we can show "Connected as ..." from
# without a second API round trip.
GSC_OAUTH_SCOPE = f"openid email {GSC_SCOPE_READONLY}"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ID_TOKEN_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}


def get_gsc_redirect_uri() -> str:
    if settings.google_gsc_redirect_uri:
        return settings.google_gsc_redirect_uri.rstrip("/")
    base = settings.api_public_url.rstrip("/")
    return f"{base}/api/search-console/oauth/callback"


def build_authorization_url(state: str, credentials: OAuthAppCredentials) -> str:
    params = {
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "response_type": "code",
        "scope": GSC_OAUTH_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str, credentials: OAuthAppCredentials) -> dict[str, Any]:
    async def _post() -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            return await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": credentials.redirect_uri,
                },
            )

    response = await with_ssl_retry(_post)
    if response.status_code >= 400:
        logger.error("GSC token exchange failed: %s", response.text)
        response.raise_for_status()
    return response.json()


async def refresh_access_token(refresh_token: str, credentials: OAuthAppCredentials) -> dict[str, Any]:
    async def _post() -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            return await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )

    response = await with_ssl_retry(_post)
    if response.status_code >= 400:
        logger.error("GSC token refresh failed: %s", response.text)
        response.raise_for_status()
    return response.json()


def get_gsc_oauth_token(db: Session, user_id: int) -> Optional[GscOAuthToken]:
    return db.query(GscOAuthToken).filter(GscOAuthToken.user_id == user_id).first()


def decode_id_token_email(id_token: str, *, expected_client_id: Optional[str] = None) -> Optional[str]:
    """Best-effort, deliberately UNVERIFIED (no JWKS signature check) extraction of
    the `email` claim from a Google-issued id_token.

    This id_token did not arrive via an untrusted browser redirect -- it came back
    as the JSON body of a direct, TLS-authenticated server-to-server POST this
    backend made to Google's token endpoint, authenticated with our own
    client_secret. That channel is already the trust boundary; a forged/tampered
    id_token would require compromising Google's token endpoint or our own
    outbound TLS, at which point the access/refresh tokens in the same response
    are already compromised too. The email is used ONLY for a display-only
    "Connected as ..." label, never for authorization or session identity, so the
    operational cost of fetching/caching Google's JWKS and handling key rotation
    isn't justified here. Basic sanity checks still apply so a malformed token
    degrades to "no email shown" rather than showing garbage. If this value is
    ever used for anything security-sensitive, switch to a verified decode against
    Google's JWKS (https://www.googleapis.com/oauth2/v3/certs).
    """
    try:
        claims = jwt.get_unverified_claims(id_token)
    except JWTError:
        logger.warning("Failed to decode GSC id_token")
        return None

    if claims.get("iss") not in GOOGLE_ID_TOKEN_ISSUERS:
        return None
    if expected_client_id and claims.get("aud") != expected_client_id:
        return None
    exp = claims.get("exp")
    if exp and datetime.now(timezone.utc).timestamp() > exp:
        return None
    if claims.get("email_verified") in (False, "false"):
        return None
    email = claims.get("email")
    return email if isinstance(email, str) and email else None


def save_gsc_tokens(
    db: Session,
    user_id: int,
    *,
    access_token: str,
    refresh_token: Optional[str],
    expires_in: Optional[int],
    account_email: Optional[str] = None,
) -> GscOAuthToken:
    expires_at = None
    if expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    record = get_gsc_oauth_token(db, user_id)
    encrypted_access = encrypt_api_key(access_token)
    encrypted_refresh = encrypt_api_key(refresh_token) if refresh_token else None

    if record:
        record.access_token = encrypted_access
        if encrypted_refresh:
            record.refresh_token = encrypted_refresh
        if account_email is not None:
            record.account_email = account_email
        record.token_expires_at = expires_at
        record.updated_at = datetime.now(timezone.utc)
    else:
        record = GscOAuthToken(
            user_id=user_id,
            access_token=encrypted_access,
            refresh_token=encrypted_refresh,
            account_email=account_email,
            token_expires_at=expires_at,
        )
        db.add(record)

    db.commit()
    db.refresh(record)
    return record


def delete_gsc_tokens(db: Session, user_id: int) -> None:
    record = get_gsc_oauth_token(db, user_id)
    if record:
        db.delete(record)
        db.commit()


def is_gsc_connected(db: Session, user_id: int) -> bool:
    record = get_gsc_oauth_token(db, user_id)
    return record is not None and bool(record.refresh_token or record.access_token)


async def get_valid_gsc_access_token(db: Session, user_id: int) -> str:
    from app.services.social_platform_app_service import get_gsc_app_credentials

    credentials = get_gsc_app_credentials(db, user_id)
    if not credentials:
        raise ValueError("Google Search Console OAuth credentials are not configured")

    record = get_gsc_oauth_token(db, user_id)
    if not record or not record.access_token:
        raise ValueError("Google Search Console is not connected")

    now = datetime.now(timezone.utc)
    expires_at = record.token_expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at and expires_at > now + timedelta(minutes=2):
        return decrypt_api_key(record.access_token)

    refresh = decrypt_api_key(record.refresh_token) if record.refresh_token else None
    if not refresh:
        if record.access_token:
            return decrypt_api_key(record.access_token)
        raise ValueError("GSC refresh token missing; reconnect Google Search Console")

    data = await refresh_access_token(refresh, credentials)
    new_access = data.get("access_token")
    if not new_access:
        raise ValueError("Failed to refresh GSC access token")

    save_gsc_tokens(
        db,
        user_id,
        access_token=new_access,
        refresh_token=data.get("refresh_token") or refresh,
        expires_in=data.get("expires_in"),
    )
    return new_access
