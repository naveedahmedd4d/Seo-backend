from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models.gsc_property import GscProperty
from app.models.seo_action_item import SeoActionItem
from app.models.user import User
from app.schemas.search_console import (
    ActionBulkRequest,
    ActionItemResponse,
    ActionListResponse,
    ActionUpdateRequest,
    GscOAuthSetupResponse,
    GscOAuthStatusResponse,
    GscOverviewResponse,
    GscPropertiesListResponse,
    GscPropertyCreate,
    GscPropertyResponse,
    GscSyncRequest,
    GscSyncResponse,
    GscSyncResult,
)
from app.services.auth_service import create_oauth_state_token, get_user_from_oauth_state
from app.services.gsc_analysis_service import build_overview
from app.services.gsc_oauth_service import (
    build_authorization_url,
    delete_gsc_tokens,
    exchange_code_for_tokens,
    get_gsc_redirect_uri,
    is_gsc_connected,
    save_gsc_tokens,
)
from app.services.gsc_service import list_sites
from app.services.gsc_oauth_service import get_valid_gsc_access_token
from app.services.gsc_sync_service import sync_all_properties, sync_property

router = APIRouter()


def _property_response(prop: GscProperty, *, is_saved: bool = True) -> GscPropertyResponse:
    return GscPropertyResponse(
        id=prop.id,
        site_url=prop.site_url,
        display_name=prop.display_name,
        wordpress_site_id=prop.wordpress_site_id,
        last_synced_at=prop.last_synced_at.isoformat() if prop.last_synced_at else None,
        is_saved=is_saved,
    )


def _action_response(item: SeoActionItem) -> ActionItemResponse:
    return ActionItemResponse(
        id=item.id,
        action_type=item.action_type,
        severity=item.severity,
        page=item.page,
        query=item.query,
        metric_snapshot=item.metric_snapshot,
        suggestion=item.suggestion,
        impact_score=item.impact_score or 0.0,
        status=item.status,
        linked_published_post_id=item.linked_published_post_id,
        linked_workflow_id=item.linked_workflow_id,
        snoozed_until=item.snoozed_until.isoformat() if item.snoozed_until else None,
        detected_at=item.detected_at.isoformat() if item.detected_at else None,
    )


def _oauth_redirect(*, error: str | None = None) -> RedirectResponse:
    frontend = settings.frontend_url.rstrip("/")
    if error:
        return RedirectResponse(f"{frontend}/dashboard/search-console?error={error}")
    return RedirectResponse(f"{frontend}/dashboard/search-console?connected=gsc")


def _get_user_property(db: Session, user_id: int, property_id: int) -> GscProperty:
    prop = (
        db.query(GscProperty)
        .filter(GscProperty.id == property_id, GscProperty.user_id == user_id)
        .first()
    )
    if not prop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    return prop


@router.get("/oauth/status", response_model=GscOAuthStatusResponse)
async def oauth_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return GscOAuthStatusResponse(connected=is_gsc_connected(db, current_user.id))


@router.get("/oauth/setup", response_model=GscOAuthSetupResponse)
async def oauth_setup(current_user: User = Depends(get_current_user)):
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
        )
    state = create_oauth_state_token(current_user, "gsc")
    return GscOAuthSetupResponse(authorization_url=build_authorization_url(state))


@router.get("/oauth/callback")
async def oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    if error:
        return _oauth_redirect(error=error)
    if not code or not state:
        return _oauth_redirect(error="missing_code")

    user = get_user_from_oauth_state(state, db, "gsc")
    if not user:
        return _oauth_redirect(error="invalid_state")

    try:
        data = await exchange_code_for_tokens(code)
        save_gsc_tokens(
            db,
            user.id,
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
        )
    except Exception as exc:
        return _oauth_redirect(error=str(exc))

    return _oauth_redirect()


@router.delete("/oauth/disconnect")
async def oauth_disconnect(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    delete_gsc_tokens(db, current_user.id)
    return {"ok": True}


@router.get("/properties", response_model=GscPropertiesListResponse)
async def list_properties(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    saved = (
        db.query(GscProperty)
        .filter(GscProperty.user_id == current_user.id)
        .order_by(GscProperty.created_at.asc())
        .all()
    )
    saved_urls = {p.site_url for p in saved}
    available: list[GscPropertyResponse] = []

    if is_gsc_connected(db, current_user.id):
        try:
            token = await get_valid_gsc_access_token(db, current_user.id)
            sites = await list_sites(token)
            for site in sites:
                url = site["site_url"]
                if url in saved_urls:
                    continue
                available.append(
                    GscPropertyResponse(
                        id=0,
                        site_url=url,
                        display_name=url,
                        is_saved=False,
                    )
                )
        except Exception:
            pass

    return GscPropertiesListResponse(
        available=available,
        saved=[_property_response(p) for p in saved],
    )


@router.post("/properties", response_model=GscPropertyResponse, status_code=status.HTTP_201_CREATED)
async def save_property(
    body: GscPropertyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = (
        db.query(GscProperty)
        .filter(
            GscProperty.user_id == current_user.id,
            GscProperty.site_url == body.site_url,
        )
        .first()
    )
    if existing:
        if body.display_name:
            existing.display_name = body.display_name
        if body.wordpress_site_id is not None:
            existing.wordpress_site_id = body.wordpress_site_id
        db.commit()
        db.refresh(existing)
        return _property_response(existing)

    prop = GscProperty(
        user_id=current_user.id,
        site_url=body.site_url,
        display_name=body.display_name or body.site_url,
        wordpress_site_id=body.wordpress_site_id,
    )
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return _property_response(prop)


@router.post("/sync", response_model=GscSyncResponse)
async def sync_data(
    body: GscSyncRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_gsc_connected(db, current_user.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GSC not connected")

    if body.property_id:
        _get_user_property(db, current_user.id, body.property_id)
        try:
            result = await sync_property(db, current_user.id, body.property_id)
            return GscSyncResponse(results=[GscSyncResult(**result)])
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    results = await sync_all_properties(db, current_user.id)
    return GscSyncResponse(results=[GscSyncResult(**r) for r in results])


@router.get("/overview", response_model=GscOverviewResponse)
async def get_overview(
    property_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_user_property(db, current_user.id, property_id)
    data = build_overview(db, current_user.id, property_id)
    return GscOverviewResponse(**data)


@router.get("/issues", response_model=ActionListResponse)
async def list_issues(
    property_id: int = Query(...),
    action_type: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await list_actions(
        property_id=property_id,
        action_type=action_type,
        status="pending_review",
        current_user=current_user,
        db=db,
    )


@router.get("/actions", response_model=ActionListResponse)
async def list_actions(
    property_id: int = Query(...),
    action_type: Optional[str] = Query(None),
    status: Optional[str] = Query("pending_review"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_user_property(db, current_user.id, property_id)
    now = datetime.now(timezone.utc)

    q = db.query(SeoActionItem).filter(
        SeoActionItem.property_id == property_id,
        SeoActionItem.user_id == current_user.id,
    )
    if action_type:
        q = q.filter(SeoActionItem.action_type == action_type)
    if status:
        q = q.filter(SeoActionItem.status == status)
        if status == "snoozed":
            q = q.filter(SeoActionItem.snoozed_until > now)
        elif status == "pending_review":
            q = q.filter(
                (SeoActionItem.snoozed_until.is_(None))
                | (SeoActionItem.snoozed_until <= now)
            )

    items = q.order_by(SeoActionItem.impact_score.desc()).all()
    open_count = (
        db.query(func.count(SeoActionItem.id))
        .filter(
            SeoActionItem.property_id == property_id,
            SeoActionItem.status == "pending_review",
        )
        .scalar()
    ) or 0

    return ActionListResponse(
        items=[_action_response(i) for i in items],
        total=len(items),
        open_count=open_count,
    )


@router.patch("/actions/{action_id}", response_model=ActionItemResponse)
async def update_action(
    action_id: int,
    body: ActionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = (
        db.query(SeoActionItem)
        .filter(SeoActionItem.id == action_id, SeoActionItem.user_id == current_user.id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")

    item.status = body.status
    if body.status == "approved":
        item.user_verified_at = datetime.now(timezone.utc)
    if body.status == "snoozed" and body.snoozed_until:
        item.snoozed_until = body.snoozed_until
    db.commit()
    db.refresh(item)
    return _action_response(item)


@router.post("/actions/bulk")
async def bulk_update_actions(
    body: ActionBulkRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = (
        db.query(SeoActionItem)
        .filter(
            SeoActionItem.id.in_(body.ids),
            SeoActionItem.user_id == current_user.id,
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    for item in items:
        item.status = body.status
        if body.status == "approved":
            item.user_verified_at = now
    db.commit()
    return {"updated": len(items)}


@router.get("/oauth/redirect-uri")
async def redirect_uri_info():
    return {"redirect_uri": get_gsc_redirect_uri()}
