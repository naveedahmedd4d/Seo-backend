"""Per-user blog generation defaults and outline templates."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.blog_settings import (
    ARTICLE_TYPES,
    BlogSettingsResponse,
    BlogSettingsUpdateRequest,
    BlogTemplateListResponse,
    BlogTemplateResponse,
    BlogTemplateUpdateRequest,
)
from app.services.blog_generation_config import (
    get_or_create_blog_settings,
    get_system_template,
    list_user_templates,
    reset_user_template,
    settings_to_response,
    upsert_blog_settings,
    upsert_user_template,
    validate_template_sections,
)

router = APIRouter()


@router.get("/blog", response_model=BlogSettingsResponse)
async def get_blog_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = get_or_create_blog_settings(db, current_user.id)
    return BlogSettingsResponse(**settings_to_response(row))


@router.put("/blog", response_model=BlogSettingsResponse)
async def update_blog_settings(
    body: BlogSettingsUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    if "faq_count" in payload and payload["faq_count"] is not None:
        if payload["faq_count"] not in (0, 3, 5, 8):
            raise HTTPException(status_code=422, detail="faq_count must be 0, 3, 5, or 8")
    if "related_query_count" in payload and payload["related_query_count"] is not None:
        if payload["related_query_count"] not in (0, 5, 8, 10):
            raise HTTPException(status_code=422, detail="related_query_count must be 0, 5, 8, or 10")
    if "target_word_count" in payload and payload["target_word_count"] is not None:
        tw = int(payload["target_word_count"])
        if tw < 800 or tw > 4000:
            raise HTTPException(status_code=422, detail="target_word_count must be 800-4000")
    row = upsert_blog_settings(db, current_user.id, **payload)
    return BlogSettingsResponse(**settings_to_response(row))


@router.get("/blog/templates", response_model=BlogTemplateListResponse)
async def get_blog_templates(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    templates = [BlogTemplateResponse(**t) for t in list_user_templates(db, current_user.id)]
    return BlogTemplateListResponse(templates=templates)


@router.get("/blog/templates/system/{article_type}", response_model=BlogTemplateResponse)
async def get_system_blog_template(
    article_type: str,
    current_user: User = Depends(get_current_user),
):
    if article_type not in ARTICLE_TYPES:
        raise HTTPException(status_code=404, detail="Unknown article type")
    return BlogTemplateResponse(**get_system_template(article_type))


@router.put("/blog/templates/{article_type}", response_model=BlogTemplateResponse)
async def save_blog_template(
    article_type: str,
    body: BlogTemplateUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if article_type not in ARTICLE_TYPES:
        raise HTTPException(status_code=404, detail="Unknown article type")
    sections = [s.model_dump() for s in body.sections]
    try:
        validate_template_sections(sections, article_type)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    fmt = body.format.model_dump() if body.format else None
    row = upsert_user_template(
        db,
        current_user.id,
        article_type,
        name=body.name,
        target_word_count=body.target_word_count,
        sections=sections,
        format_dict=fmt,
    )
    return BlogTemplateResponse(
        article_type=row.article_type,
        name=row.name,
        is_default=bool(row.is_default),
        is_system=False,
        target_word_count=row.target_word_count,
        sections=row.sections,
        format=row.format_options or {},
    )


@router.post("/blog/templates/{article_type}/reset", response_model=BlogTemplateResponse)
async def reset_blog_template(
    article_type: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if article_type not in ARTICLE_TYPES:
        raise HTTPException(status_code=404, detail="Unknown article type")
    payload = reset_user_template(db, current_user.id, article_type)
    return BlogTemplateResponse(**payload)
