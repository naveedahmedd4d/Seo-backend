from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.content_analytics import GaOAuthToken, GaPageMetric, GaProperty
from app.services.gsc_oauth_service import (
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
    decode_id_token_email,
)
from app.services.http_ssl import httpx_verify
from app.services.social_platform_app_service import OAuthAppCredentials, get_gsc_app_credentials
from app.services.wordpress_service import decrypt_api_key, encrypt_api_key

logger = logging.getLogger(__name__)

GA_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


def get_ga_redirect_uri() -> str:
    if settings.google_ga_redirect_uri:
        return settings.google_ga_redirect_uri.rstrip("/")
    return f"{settings.api_public_url.rstrip('/')}/api/google-analytics/oauth/callback"


def get_ga_credentials(db: Session, user_id: int) -> OAuthAppCredentials | None:
    return get_gsc_app_credentials(db, user_id, redirect_uri=get_ga_redirect_uri())


def build_ga_authorization_url(state: str, credentials: OAuthAppCredentials) -> str:
    return f"{GOOGLE_AUTH_URL}?{urlencode({
        'client_id': credentials.client_id,
        'redirect_uri': credentials.redirect_uri,
        'response_type': 'code',
        'scope': f'openid email {GA_SCOPE}',
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
    })}"


async def exchange_ga_code(code: str, credentials: OAuthAppCredentials) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": credentials.redirect_uri,
        })
    response.raise_for_status()
    return response.json()


def save_ga_tokens(db: Session, user_id: int, payload: dict[str, Any]) -> GaOAuthToken:
    row = db.query(GaOAuthToken).filter(GaOAuthToken.user_id == user_id).first()
    if not row:
        row = GaOAuthToken(user_id=user_id)
        db.add(row)
    row.access_token = encrypt_api_key(payload["access_token"])
    if payload.get("refresh_token"):
        row.refresh_token = encrypt_api_key(payload["refresh_token"])
    row.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in") or 3600))
    if payload.get("id_token"):
        credentials = get_ga_credentials(db, user_id)
        row.account_email = decode_id_token_email(
            payload["id_token"], expected_client_id=credentials.client_id if credentials else None
        )
    db.commit()
    db.refresh(row)
    return row


async def get_valid_ga_access_token(db: Session, user_id: int) -> str:
    row = db.query(GaOAuthToken).filter(GaOAuthToken.user_id == user_id).first()
    if not row or not row.access_token:
        raise ValueError("Google Analytics is not connected")
    if not row.token_expires_at or row.token_expires_at > datetime.now(timezone.utc) + timedelta(minutes=2):
        return decrypt_api_key(row.access_token)
    if not row.refresh_token:
        raise ValueError("Google Analytics authorization expired; reconnect the account")
    credentials = get_ga_credentials(db, user_id)
    if not credentials:
        raise ValueError("Google OAuth credentials are unavailable")
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "refresh_token": decrypt_api_key(row.refresh_token),
            "grant_type": "refresh_token",
        })
    response.raise_for_status()
    payload = response.json()
    row.access_token = encrypt_api_key(payload["access_token"])
    row.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in") or 3600))
    db.commit()
    return payload["access_token"]


async def list_ga_properties(db: Session, user_id: int) -> list[dict[str, Any]]:
    token = await get_valid_ga_access_token(db, user_id)
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
            headers={"Authorization": f"Bearer {token}"},
            params={"pageSize": 200},
        )
    response.raise_for_status()
    output: list[dict[str, Any]] = []
    for account in response.json().get("accountSummaries") or []:
        for prop in account.get("propertySummaries") or []:
            resource = str(prop.get("property") or "")
            property_id = resource.rsplit("/", 1)[-1]
            if property_id:
                output.append({
                    "property_id": property_id,
                    "display_name": prop.get("displayName") or property_id,
                    "account": account.get("displayName"),
                })
    return output


async def _run_report(token: str, property_id: str, start: date, end: date) -> list[dict[str, Any]]:
    body = {
        "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
        "dimensions": [{"name": "date"}, {"name": "pagePath"}, {"name": "sessionPrimaryChannelGroup"}],
        "metrics": [{"name": "screenPageViews"}, {"name": "sessions"}, {"name": "activeUsers"}],
        "limit": "250000",
        "keepEmptyRows": False,
    }
    async with httpx.AsyncClient(timeout=90.0, verify=httpx_verify()) as client:
        response = await client.post(
            f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
    response.raise_for_status()
    return response.json().get("rows") or []


async def sync_ga_property(db: Session, user_id: int, prop: GaProperty) -> dict[str, Any]:
    token = await get_valid_ga_access_token(db, user_id)
    end = date.today()
    start = end - timedelta(days=89)
    rows = await _run_report(token, prop.property_id, start, end)
    grouped: dict[tuple[date, str], dict[str, int]] = {}
    for row in rows:
        dimensions = row.get("dimensionValues") or []
        metrics = row.get("metricValues") or []
        if len(dimensions) < 3 or len(metrics) < 3:
            continue
        raw_date, path, channel = (str(item.get("value") or "") for item in dimensions[:3])
        if len(raw_date) != 8:
            continue
        metric_date = datetime.strptime(raw_date, "%Y%m%d").date()
        key = (metric_date, path or "/")
        agg = grouped.setdefault(key, {"views": 0, "sessions": 0, "active_users": 0, "organic_sessions": 0})
        views, sessions, users = (int(float(item.get("value") or 0)) for item in metrics[:3])
        agg["views"] += views
        agg["sessions"] += sessions
        agg["active_users"] += users
        if channel.lower() == "organic search":
            agg["organic_sessions"] += sessions
    for (metric_date, path), values in grouped.items():
        metric = db.query(GaPageMetric).filter(
            GaPageMetric.property_id == prop.id,
            GaPageMetric.date == metric_date,
            GaPageMetric.page_path == path,
        ).first()
        if not metric:
            metric = GaPageMetric(property_id=prop.id, date=metric_date, page_path=path)
            db.add(metric)
        for key, value in values.items():
            setattr(metric, key, value)
    prop.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return {"property_id": prop.id, "rows_synced": len(grouped), "data_through": end.isoformat()}


async def sync_all_ga_properties(db: Session, user_id: int) -> list[dict[str, Any]]:
    properties = db.query(GaProperty).filter(GaProperty.user_id == user_id).all()
    results = []
    for prop in properties:
        try:
            results.append(await sync_ga_property(db, user_id, prop))
        except Exception as exc:
            logger.warning("GA sync failed for property %s: %s", prop.id, exc)
            results.append({"property_id": prop.id, "error": str(exc)})
    return results
