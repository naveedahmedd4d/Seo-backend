import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.services.http_ssl import httpx_verify
from app.services.oauth_pkce import generate_pkce_pair
from app.services.social_account_service import (
    get_account_metadata,
    get_decrypted_access_token,
    get_decrypted_refresh_token,
    get_user_social_account,
    upsert_social_account_tokens,
)
from app.services.social_platform_app_service import OAuthAppCredentials, get_x_app_credentials

logger = logging.getLogger(__name__)

# Prefer api.x.com / x.com hosts (twitter.com still aliases, but X docs use these).
X_AUTH_URL = "https://x.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
X_API_BASE = "https://api.x.com/2"

# Omit media.write at connect — Free/Basic X apps often hang or reject the authorize
# screen when it is requested. Text posts work; image upload uses v2 when the token
# already has media.write (Elevated/paid), otherwise we post text-only.
X_SCOPES = "tweet.read tweet.write users.read offline.access"
X_MEDIA_UPLOAD_URL = "https://api.x.com/2/media/upload"
X_MEDIA_CHUNK_BYTES = 4 * 1024 * 1024


def build_authorization_url(
    credentials: OAuthAppCredentials,
    state: str,
    *,
    code_challenge: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "scope": X_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{X_AUTH_URL}?{urlencode(params)}"


def _basic_auth_header(credentials: OAuthAppCredentials) -> str:
    raw = f"{credentials.client_id}:{credentials.client_secret}"
    return base64.b64encode(raw.encode()).decode()


def _x_oauth_error_message(response: httpx.Response) -> str:
    """Turn an X token/API error body into a short user-facing message."""
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = str(
                payload.get("error_description")
                or payload.get("error")
                or payload.get("detail")
                or ""
            ).strip()
    except Exception:
        detail = (response.text or "").strip()[:200]
    lower = detail.lower()
    if response.status_code in {400, 401} and (
        "invalid_grant" in lower
        or "expired" in lower
        or ("code" in lower and ("invalid" in lower or "expired" in lower))
    ):
        return (
            "X authorization code expired or was already used. "
            "The API host may have been waking from sleep — click Connect again "
            "and approve promptly."
        )
    if "redirect_uri" in lower:
        return (
            "X redirect URI mismatch. In the X Developer Portal, set the callback to "
            "exactly the Redirect URI shown in the Connect dialog."
        )
    if "invalid_client" in lower or "unauthorized_client" in lower:
        return (
            "X rejected the Client ID/secret. Use OAuth 2.0 Client ID and Client Secret "
            "(User authentication settings), not the older API Key / API Key Secret."
        )
    if "invalid_scope" in lower or ("scope" in lower and "invalid" in lower):
        return (
            "X rejected the requested scopes. Enable Read and Write in the app, "
            "and confirm OAuth 2.0 is turned on."
        )
    if detail:
        return f"X token exchange failed ({response.status_code}): {detail[:220]}"
    return f"X token exchange failed ({response.status_code})"


async def _exchange_token(
    credentials: OAuthAppCredentials,
    data: dict[str, str],
    *,
    use_basic_auth: bool = True,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    body = dict(data)
    if use_basic_auth:
        headers["Authorization"] = f"Basic {_basic_auth_header(credentials)}"
    else:
        # Public clients authenticate with client_id in the body (no Basic header).
        body.setdefault("client_id", credentials.client_id)

    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(X_TOKEN_URL, data=body, headers=headers)
    if response.status_code >= 400:
        logger.error(
            "X token exchange failed basic_auth=%s status=%s body=%s",
            use_basic_auth,
            response.status_code,
            response.text[:500],
        )
        raise ValueError(_x_oauth_error_message(response))
    return response.json()


async def exchange_code_for_tokens(
    credentials: OAuthAppCredentials,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": credentials.redirect_uri,
        "code_verifier": code_verifier,
    }
    try:
        return await _exchange_token(credentials, payload, use_basic_auth=True)
    except ValueError as first_exc:
        # Some X apps are registered as public clients — retry without Basic auth.
        message = str(first_exc).lower()
        if "invalid_client" in message or "unauthorized_client" in message:
            logger.warning("X confidential token exchange failed; retrying as public client")
            return await _exchange_token(credentials, payload, use_basic_auth=False)
        raise


async def refresh_access_token(
    credentials: OAuthAppCredentials,
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


async def fetch_user_profile(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{X_API_BASE}/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"user.fields": "username,name"},
        )
    if response.status_code >= 400:
        logger.error("X profile fetch failed: %s", response.text)
        response.raise_for_status()
    return response.json().get("data") or {}


def _x_response_detail(response: httpx.Response) -> str:
    try:
        err = response.json()
        if isinstance(err, dict):
            detail = (
                err.get("detail")
                or err.get("title")
                or err.get("error")
                or (err.get("errors") or [{}])[0].get("message")
                or ""
            )
            if detail:
                return str(detail)
    except Exception:
        pass
    return (response.text or "").strip()[:300] or response.reason_phrase or "error"


def _extract_media_id(payload: dict[str, Any]) -> Optional[str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if data:
        media_id = data.get("id") or data.get("media_id_string") or data.get("media_id")
        if media_id:
            return str(media_id)
    media_id = payload.get("media_id_string") or payload.get("media_id") or payload.get("id")
    return str(media_id) if media_id else None


def _media_upload_error(response: httpx.Response, step: str) -> ValueError:
    detail = _x_response_detail(response)
    logger.error("X media upload %s failed %s: %s", step, response.status_code, detail)
    if response.status_code in {401, 403}:
        return ValueError(
            f"X media upload forbidden ({response.status_code}) during {step}: {detail}. "
            "v1.1 media upload is retired; Free/Basic apps often cannot attach images. "
            "The tweet can still publish as text-only."
        )
    return ValueError(f"X media upload failed during {step} ({response.status_code}): {detail}")


async def upload_media(access_token: str, image_bytes: bytes, media_type: str = "image/png") -> str:
    """Upload image via X API v2 media upload (INIT / APPEND / FINALIZE)."""
    if not image_bytes:
        raise ValueError("X media upload requires image bytes")

    headers = {"Authorization": f"Bearer {access_token}"}
    total_bytes = len(image_bytes)

    async with httpx.AsyncClient(timeout=90.0, verify=httpx_verify()) as client:
        init_response = await client.post(
            X_MEDIA_UPLOAD_URL,
            headers={**headers, "Content-Type": "application/json"},
            json={
                "command": "INIT",
                "media_type": media_type,
                "total_bytes": total_bytes,
                "media_category": "tweet_image",
            },
        )
        if init_response.status_code >= 400:
            # Some X builds expect form-encoded INIT instead of JSON.
            init_response = await client.post(
                X_MEDIA_UPLOAD_URL,
                headers=headers,
                data={
                    "command": "INIT",
                    "media_type": media_type,
                    "total_bytes": str(total_bytes),
                    "media_category": "tweet_image",
                },
            )
        if init_response.status_code >= 400:
            raise _media_upload_error(init_response, "INIT")

        try:
            init_payload = init_response.json() if init_response.content else {}
        except Exception:
            init_payload = {}
        media_id = _extract_media_id(init_payload if isinstance(init_payload, dict) else {})
        if not media_id:
            raise ValueError("X media INIT did not return a media id")

        for segment_index, start in enumerate(range(0, total_bytes, X_MEDIA_CHUNK_BYTES)):
            chunk = image_bytes[start : start + X_MEDIA_CHUNK_BYTES]
            append_response = await client.post(
                X_MEDIA_UPLOAD_URL,
                headers=headers,
                data={
                    "command": "APPEND",
                    "media_id": media_id,
                    "segment_index": str(segment_index),
                },
                files={"media": (f"chunk_{segment_index}.bin", chunk, media_type)},
            )
            # APPEND often returns 204 No Content.
            if append_response.status_code >= 400:
                raise _media_upload_error(append_response, "APPEND")

        finalize_response = await client.post(
            X_MEDIA_UPLOAD_URL,
            headers=headers,
            data={
                "command": "FINALIZE",
                "media_id": media_id,
            },
        )
        if finalize_response.status_code >= 400:
            finalize_response = await client.post(
                X_MEDIA_UPLOAD_URL,
                headers={**headers, "Content-Type": "application/json"},
                json={"command": "FINALIZE", "media_id": media_id},
            )
        if finalize_response.status_code >= 400:
            raise _media_upload_error(finalize_response, "FINALIZE")

        try:
            final_payload = finalize_response.json() if finalize_response.content else {}
        except Exception:
            final_payload = {}
        if isinstance(final_payload, dict):
            data = final_payload.get("data") if isinstance(final_payload.get("data"), dict) else final_payload
            processing = data.get("processing_info") if isinstance(data, dict) else None
            if isinstance(processing, dict) and processing.get("state") in {"pending", "in_progress"}:
                # Images are usually sync; poll briefly if X asks.
                for _ in range(5):
                    wait_s = int(processing.get("check_after_secs") or 1)
                    await asyncio.sleep(min(max(wait_s, 1), 5))
                    status_response = await client.get(
                        X_MEDIA_UPLOAD_URL,
                        headers=headers,
                        params={"command": "STATUS", "media_id": media_id},
                    )
                    if status_response.status_code >= 400:
                        break
                    try:
                        status_payload = status_response.json()
                    except Exception:
                        break
                    status_data = (
                        status_payload.get("data")
                        if isinstance(status_payload.get("data"), dict)
                        else status_payload
                    )
                    processing = (
                        status_data.get("processing_info")
                        if isinstance(status_data, dict)
                        else None
                    )
                    if not isinstance(processing, dict):
                        break
                    state = processing.get("state")
                    if state == "succeeded":
                        break
                    if state == "failed":
                        raise ValueError(
                            f"X media processing failed: {processing.get('error') or processing}"
                        )

    return media_id


async def create_tweet(
    access_token: str,
    text: str,
    *,
    media_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"text": text[:280]}
    if media_ids:
        body["media"] = {"media_ids": media_ids}
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"{X_API_BASE}/tweets",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
    if response.status_code >= 400:
        detail = ""
        try:
            err = response.json()
            detail = (
                err.get("detail")
                or err.get("title")
                or (err.get("errors") or [{}])[0].get("message")
                or ""
            )
        except Exception:
            detail = ""
        if not detail:
            detail = (response.text or "").strip()[:300]
        logger.error("X tweet failed %s: %s", response.status_code, detail)
        if response.status_code == 403:
            raise ValueError(
                f"X rejected the tweet (403): {detail or 'Forbidden'}. "
                "In the X Developer Portal set app permissions to Read and Write, "
                "then disconnect and reconnect X in Social accounts."
            )
        raise ValueError(f"X API error ({response.status_code}): {detail or response.reason_phrase}")
    return response.json().get("data") or {}


async def save_x_account_from_code(
    db: Session,
    user_id: int,
    code: str,
    code_verifier: str,
    *,
    redirect_uri: Optional[str] = None,
) -> SocialAccount:
    credentials = get_x_app_credentials(db, user_id, redirect_uri=redirect_uri)
    if not credentials:
        raise ValueError("X app credentials not found. Connect again from Social accounts.")

    token_payload = await exchange_code_for_tokens(credentials, code, code_verifier)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    expires_at = _expires_at_from_token_payload(token_payload)

    profile: dict[str, Any] = {}
    try:
        profile = await fetch_user_profile(access_token)
    except Exception as exc:
        # Token is still valid — don't abandon the connection if /users/me fails.
        logger.warning("X profile fetch failed after token exchange: %s", exc)

    username = profile.get("username") or profile.get("name") or "X"
    label = f"@{username}" if username and not str(username).startswith("@") else str(username)

    return upsert_social_account_tokens(
        db,
        user_id,
        "x",
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        account_label=label,
        metadata={
            "username": username,
            "profile": profile,
            "granted_scope": token_payload.get("scope"),
        },
    )


async def get_valid_x_access_token(db: Session, user_id: int) -> str:
    account = get_user_social_account(db, user_id, "x")
    if not account:
        raise ValueError("X account not connected")

    credentials = get_x_app_credentials(db, user_id)
    if not credentials:
        raise ValueError("X app credentials missing")

    metadata = get_account_metadata(account)
    granted = str(metadata.get("granted_scope") or "")
    if granted and "tweet.write" not in granted.split():
        raise ValueError(
            f"X token scopes [{granted}] are missing tweet.write. "
            "Set the X app to Read and Write in the Developer Portal, "
            "then disconnect and reconnect X in Social accounts."
        )

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
                    "x",
                    access_token=token_payload["access_token"],
                    refresh_token=token_payload.get("refresh_token") or refresh_token,
                    token_expires_at=_expires_at_from_token_payload(token_payload),
                    account_label=account.account_label,
                    metadata=metadata,
                )
                return token_payload["access_token"]

    return get_decrypted_access_token(account)


def start_x_oauth_pkce() -> tuple[str, str]:
    return generate_pkce_pair()
