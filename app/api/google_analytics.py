from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models.content_analytics import GaOAuthToken, GaProperty
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.services.auth_service import create_oauth_state_token, decode_oauth_state, get_user_by_email
from app.services.ga_analytics_service import (
    build_ga_authorization_url,
    exchange_ga_code,
    get_ga_credentials,
    get_ga_redirect_uri,
    list_ga_properties,
    save_ga_tokens,
)

router = APIRouter()


class GaPropertySave(BaseModel):
    property_id: str
    display_name: Optional[str] = None
    wordpress_site_id: Optional[int] = None


@router.get("/oauth/status")
async def oauth_status(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = db.query(GaOAuthToken).filter(GaOAuthToken.user_id == current_user.id).first()
    return {"connected": bool(token and (token.refresh_token or token.access_token)), "account_email": token.account_email if token else None}


@router.get("/oauth/setup")
async def oauth_setup(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    credentials = get_ga_credentials(db, current_user.id)
    return {"redirect_uri": get_ga_redirect_uri(), "available": credentials is not None}


@router.post("/oauth/connect")
async def oauth_connect(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    credentials = get_ga_credentials(db, current_user.id)
    if not credentials:
        raise HTTPException(status_code=400, detail="Configure Google OAuth credentials in Search Performance first")
    state_token = create_oauth_state_token(current_user, "ga")
    return {"authorization_url": build_ga_authorization_url(state_token, credentials)}


@router.get("/oauth/callback")
async def oauth_callback(
    code: Optional[str] = Query(None),
    state_token: Optional[str] = Query(None, alias="state"),
    error: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    frontend = settings.frontend_url.rstrip("/")
    if error or not code or not state_token:
        return RedirectResponse(f"{frontend}/dashboard/content-analytics?ga_error=authorization_failed")
    user_payload, _ = decode_oauth_state(state_token, "ga")
    if not user_payload:
        return RedirectResponse(f"{frontend}/dashboard/content-analytics?ga_error=invalid_state")
    user = get_user_by_email(db, user_payload["email"])
    if not user:
        return RedirectResponse(f"{frontend}/dashboard/content-analytics?ga_error=invalid_user")
    credentials = get_ga_credentials(db, user.id)
    if not credentials:
        return RedirectResponse(f"{frontend}/dashboard/content-analytics?ga_error=missing_credentials")
    try:
        payload = await exchange_ga_code(code, credentials)
        save_ga_tokens(db, user.id, payload)
    except Exception:
        return RedirectResponse(f"{frontend}/dashboard/content-analytics?ga_error=token_exchange_failed")
    return RedirectResponse(f"{frontend}/dashboard/content-analytics?ga_connected=1")


@router.delete("/oauth/disconnect")
async def oauth_disconnect(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(GaOAuthToken).filter(GaOAuthToken.user_id == current_user.id).delete()
    db.commit()
    return {"ok": True}


@router.get("/properties")
async def properties(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    saved = db.query(GaProperty).filter(GaProperty.user_id == current_user.id).order_by(GaProperty.display_name).all()
    available = []
    try:
        available = await list_ga_properties(db, current_user.id)
    except Exception as exc:
        if not saved:
            return {"available": [], "saved": [], "error": str(exc)}
    saved_ids = {row.property_id for row in saved}
    return {
        "available": [row for row in available if row["property_id"] not in saved_ids],
        "saved": [{
            "id": row.id, "property_id": row.property_id, "display_name": row.display_name,
            "wordpress_site_id": row.wordpress_site_id,
            "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
        } for row in saved],
    }


@router.post("/properties", status_code=status.HTTP_201_CREATED)
async def save_property(body: GaPropertySave, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.wordpress_site_id is not None:
        owned = db.query(WordPressSite).filter(
            WordPressSite.id == body.wordpress_site_id,
            WordPressSite.user_id == current_user.id,
        ).first()
        if not owned:
            raise HTTPException(status_code=404, detail="WordPress site not found")
    row = db.query(GaProperty).filter(
        GaProperty.user_id == current_user.id,
        GaProperty.property_id == body.property_id,
    ).first()
    if not row:
        row = GaProperty(user_id=current_user.id, property_id=body.property_id)
        db.add(row)
    row.display_name = body.display_name or body.property_id
    row.wordpress_site_id = body.wordpress_site_id
    db.commit()
    db.refresh(row)
    return {"id": row.id, "property_id": row.property_id, "display_name": row.display_name, "wordpress_site_id": row.wordpress_site_id}
