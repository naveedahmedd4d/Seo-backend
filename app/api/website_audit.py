"""Website Audit API — Phase 1 MVP + Blog Content Review."""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.blog_content_audit import BlogContentAudit
from app.models.blog_content_audit_fix import BlogContentAuditFix
from app.models.blog_content_audit_run import BlogContentAuditRun
from app.models.user import User
from app.models.website_audit_issue import WebsiteAuditIssue
from app.models.website_audit_page import WebsiteAuditPage
from app.models.website_audit_run import WebsiteAuditRun
from app.models.wordpress_site import WordPressSite
from app.schemas.blog_content_audit import (
    BlogContentAuditDetailResponse,
    BlogContentAuditFinding,
    BlogContentAuditFixEditRequest,
    BlogContentAuditFixGenerateRequest,
    BlogContentAuditFixListResponse,
    BlogContentAuditFixPreviewCombinedRequest,
    BlogContentAuditFixPreviewCombinedResponse,
    BlogContentAuditFixResponse,
    BlogContentAuditGapItem,
    BlogContentAuditListItem,
    BlogContentAuditListResponse,
    BlogContentAuditProgress,
    BlogContentAuditRunResponse,
    BlogContentAuditSiteSummary,
    BlogContentAuditStartRequest,
)
from app.schemas.website_audit import (
    WebsiteAuditIssueListResponse,
    WebsiteAuditIssueResponse,
    WebsiteAuditPageListResponse,
    WebsiteAuditPageResponse,
    WebsiteAuditRunResponse,
    WebsiteAuditSiteOption,
    WebsiteAuditStartRequest,
)
from app.services.website_audit_discover import DEFAULT_MAX_PAGES, HARD_MAX_PAGES
from app.services.website_audit_service import (
    bg_run_website_audit,
    get_run_for_user,
    has_running_audit,
)
from app.services.blog_content_audit_config import DEFAULT_POST_LIMIT, HARD_MAX_POSTS
from app.services.blog_content_audit_service import (
    bg_run_blog_content_audit,
    get_active_or_latest_content_audit,
    get_content_audit_run,
    get_latest_content_audit,
    has_running_content_audit,
)

router = APIRouter()
content_router = APIRouter(prefix="/content-audit", tags=["blog-content-audit"])


def _get_user_site(db: Session, user_id: int, site_id: int) -> WordPressSite:
    site = (
        db.query(WordPressSite)
        .filter(WordPressSite.id == site_id, WordPressSite.user_id == user_id)
        .first()
    )
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WordPress site not found")
    return site


def _run_response(run: WebsiteAuditRun) -> WebsiteAuditRunResponse:
    return WebsiteAuditRunResponse(
        id=run.id,
        wordpress_site_id=run.wordpress_site_id,
        status=run.status,
        stage=run.stage,
        total_urls=run.total_urls,
        processed_urls=run.processed_urls,
        health_score=run.health_score,
        category_scores=run.category_scores,
        scope_json=run.scope_json,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.get("/sites", response_model=list[WebsiteAuditSiteOption])
async def list_audit_sites(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sites = (
        db.query(WordPressSite)
        .filter(WordPressSite.user_id == current_user.id)
        .order_by(WordPressSite.name.asc())
        .all()
    )
    return [WebsiteAuditSiteOption(id=s.id, name=s.name, url=s.url) for s in sites]


@router.post("/runs", response_model=WebsiteAuditRunResponse, status_code=status.HTTP_201_CREATED)
async def start_website_audit(
    body: WebsiteAuditStartRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = _get_user_site(db, current_user.id, body.wordpress_site_id)
    if has_running_audit(db, site.id, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Website audit already running for this site",
        )

    max_pages = max(1, min(body.max_pages or DEFAULT_MAX_PAGES, HARD_MAX_PAGES))
    run = WebsiteAuditRun(
        user_id=current_user.id,
        wordpress_site_id=site.id,
        status="pending",
        stage="discover",
        scope_json={
            "max_pages": max_pages,
            "content_types": body.content_types,
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    background_tasks.add_task(bg_run_website_audit, current_user.id, run.id)
    return _run_response(run)


@router.get("/runs", response_model=list[WebsiteAuditRunResponse])
async def list_website_audit_runs(
    wordpress_site_id: Optional[int] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(WebsiteAuditRun).filter(WebsiteAuditRun.user_id == current_user.id)
    if wordpress_site_id is not None:
        _get_user_site(db, current_user.id, wordpress_site_id)
        q = q.filter(WebsiteAuditRun.wordpress_site_id == wordpress_site_id)
    runs = q.order_by(WebsiteAuditRun.started_at.desc()).limit(limit).all()
    return [_run_response(r) for r in runs]


@router.get("/runs/{run_id}", response_model=WebsiteAuditRunResponse)
async def get_website_audit_run(
    run_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = get_run_for_user(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit run not found")
    return _run_response(run)


@router.get("/runs/{run_id}/issues", response_model=WebsiteAuditIssueListResponse)
async def list_website_audit_issues(
    run_id: int,
    severity: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = get_run_for_user(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit run not found")

    query = db.query(WebsiteAuditIssue).filter(WebsiteAuditIssue.run_id == run.id)
    if severity:
        query = query.filter(WebsiteAuditIssue.severity == severity.lower())
    if category:
        query = query.filter(WebsiteAuditIssue.category == category.lower())
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            (WebsiteAuditIssue.url.ilike(like))
            | (WebsiteAuditIssue.title.ilike(like))
            | (WebsiteAuditIssue.issue_code.ilike(like))
        )

    total = query.count()
    items = (
        query.order_by(WebsiteAuditIssue.priority_score.desc(), WebsiteAuditIssue.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return WebsiteAuditIssueListResponse(
        items=[WebsiteAuditIssueResponse.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/runs/{run_id}/pages", response_model=WebsiteAuditPageListResponse)
async def list_website_audit_pages(
    run_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = get_run_for_user(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit run not found")

    query = db.query(WebsiteAuditPage).filter(WebsiteAuditPage.run_id == run.id)
    total = query.count()
    items = (
        query.order_by(WebsiteAuditPage.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return WebsiteAuditPageListResponse(
        items=[WebsiteAuditPageResponse.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# --- Blog Content Review (Phase A) ---


def _content_run_response(run: BlogContentAuditRun) -> BlogContentAuditRunResponse:
    progress = run.progress_json or {}
    summary = run.site_summary or {}
    return BlogContentAuditRunResponse(
        id=run.id,
        wordpress_site_id=run.wordpress_site_id,
        website_audit_run_id=run.website_audit_run_id,
        status=run.status,
        progress=BlogContentAuditProgress(**progress) if progress else None,
        site_summary=BlogContentAuditSiteSummary(**summary) if summary else None,
        scope_json=run.scope_json,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _top_findings(findings: list | None, limit: int = 3) -> list[BlogContentAuditFinding]:
    if not findings:
        return []
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_f = sorted(
        findings,
        key=lambda f: severity_order.get(str(f.get("severity", "low")).lower(), 9),
    )
    return [
        BlogContentAuditFinding(
            severity=str(f.get("severity", "medium")),
            category=str(f.get("category", "")),
            message=str(f.get("message", "")),
            evidence=f.get("evidence"),
        )
        for f in sorted_f[:limit]
        if isinstance(f, dict)
    ]


@content_router.post("/runs", response_model=BlogContentAuditRunResponse, status_code=status.HTTP_201_CREATED)
async def start_content_audit(
    body: BlogContentAuditStartRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = _get_user_site(db, current_user.id, body.wordpress_site_id)
    if has_running_content_audit(db, site.id, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Content audit already running for this site",
        )

    if body.website_audit_run_id is not None:
        wa = get_run_for_user(db, body.website_audit_run_id, current_user.id)
        if not wa:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website audit run not found")
        if wa.wordpress_site_id != site.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Website audit run does not belong to this site",
            )

    post_limit = max(1, min(body.post_limit or DEFAULT_POST_LIMIT, HARD_MAX_POSTS))
    run = BlogContentAuditRun(
        user_id=current_user.id,
        wordpress_site_id=site.id,
        website_audit_run_id=body.website_audit_run_id,
        status="pending",
        scope_json={
            "post_limit": post_limit,
            "post_ids": body.post_ids,
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    background_tasks.add_task(bg_run_blog_content_audit, current_user.id, run.id)
    return _content_run_response(run)


@content_router.get("/runs/{run_id}", response_model=BlogContentAuditRunResponse)
async def get_content_audit_run_route(
    run_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = get_content_audit_run(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content audit run not found")
    return _content_run_response(run)


@content_router.get("/runs/{run_id}/posts", response_model=BlogContentAuditListResponse)
async def list_content_audit_posts(
    run_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = get_content_audit_run(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content audit run not found")

    query = db.query(BlogContentAudit).filter(BlogContentAudit.run_id == run.id)
    total = query.count()
    items = (
        query.order_by(BlogContentAudit.overall_score.asc().nullsfirst(), BlogContentAudit.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    list_items = [
        BlogContentAuditListItem(
            id=a.id,
            run_id=a.run_id,
            wp_post_id=a.wp_post_id,
            url=a.url,
            title=a.title,
            slug=a.slug,
            article_type=a.article_type,
            word_count=a.word_count,
            word_target=a.word_target,
            word_target_met=a.word_target_met,
            overall_score=a.overall_score,
            top_findings=_top_findings(a.findings),
        )
        for a in items
    ]
    return BlogContentAuditListResponse(
        items=list_items,
        total=total,
        page=page,
        page_size=page_size,
    )


@content_router.get("/runs/{run_id}/posts/{audit_id}", response_model=BlogContentAuditDetailResponse)
async def get_content_audit_post_detail(
    run_id: int,
    audit_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = get_content_audit_run(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content audit run not found")

    audit = (
        db.query(BlogContentAudit)
        .filter(BlogContentAudit.run_id == run.id, BlogContentAudit.id == audit_id)
        .first()
    )
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post audit not found")

    return BlogContentAuditDetailResponse(
        id=audit.id,
        run_id=audit.run_id,
        wp_post_id=audit.wp_post_id,
        url=audit.url,
        title=audit.title,
        slug=audit.slug,
        article_type=audit.article_type,
        word_count=audit.word_count,
        word_target=audit.word_target,
        word_target_met=audit.word_target_met,
        seo_score=audit.seo_score,
        content_quality_score=audit.content_quality_score,
        search_intent_score=audit.search_intent_score,
        content_depth_score=audit.content_depth_score,
        originality_score=audit.originality_score,
        eeat_score=audit.eeat_score,
        adsense_quality_score=audit.adsense_quality_score,
        overall_score=audit.overall_score,
        findings=_top_findings(audit.findings, limit=50),
        gap_matrix=[
            BlogContentAuditGapItem(
                topic=str(g.get("topic", "")),
                status=g.get("status", "missing"),
                evidence=g.get("evidence"),
            )
            for g in (audit.gap_matrix or [])
            if isinstance(g, dict)
        ],
        adsense_risks=audit.adsense_risks or [],
        content_hash=audit.content_hash,
        recommended_topics=_recommended_topics(audit),
    )


def _recommended_topics(audit: BlogContentAudit) -> list[str]:
    payload = audit.audit_payload if isinstance(audit.audit_payload, dict) else {}
    topics = payload.get("recommended_topics") or []
    return [str(t) for t in topics if t][:8]


def _fix_response(fix: BlogContentAuditFix) -> BlogContentAuditFixResponse:
    return BlogContentAuditFixResponse(
        id=fix.id,
        audit_id=fix.audit_id,
        fix_type=fix.fix_type,
        target_field=fix.target_field,
        title=fix.title,
        description=fix.description,
        severity=fix.severity,
        status=fix.status,
        finding_refs=fix.finding_refs or [],
        before_text=fix.before_text,
        after_proposed=fix.after_proposed,
        diff_segments=fix.diff_segments or [],
        generator_meta=fix.generator_meta,
        created_at=fix.created_at,
        updated_at=fix.updated_at,
    )


@content_router.post(
    "/runs/{run_id}/posts/{audit_id}/fixes/generate",
    response_model=BlogContentAuditFixListResponse,
)
async def generate_content_audit_fixes(
    run_id: int,
    audit_id: int,
    body: BlogContentAuditFixGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.blog_content_audit_fix_service import generate_fixes

    try:
        fixes = await generate_fixes(
            db,
            run_id=run_id,
            audit_id=audit_id,
            user_id=current_user.id,
            force=body.force,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post audit not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return BlogContentAuditFixListResponse(
        items=[_fix_response(f) for f in fixes],
        total=len(fixes),
    )


@content_router.get(
    "/runs/{run_id}/posts/{audit_id}/fixes",
    response_model=BlogContentAuditFixListResponse,
)
async def list_content_audit_fixes(
    run_id: int,
    audit_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.blog_content_audit_fix_service import get_audit_for_user

    audit = get_audit_for_user(db, run_id, audit_id, current_user.id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post audit not found")
    fixes = (
        db.query(BlogContentAuditFix)
        .filter(BlogContentAuditFix.audit_id == audit.id)
        .order_by(BlogContentAuditFix.id.asc())
        .all()
    )
    return BlogContentAuditFixListResponse(
        items=[_fix_response(f) for f in fixes],
        total=len(fixes),
    )


@content_router.get(
    "/runs/{run_id}/posts/{audit_id}/fixes/{fix_id}",
    response_model=BlogContentAuditFixResponse,
)
async def get_content_audit_fix(
    run_id: int,
    audit_id: int,
    fix_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.blog_content_audit_fix_service import get_audit_for_user, get_fix_for_audit

    audit = get_audit_for_user(db, run_id, audit_id, current_user.id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post audit not found")
    fix = get_fix_for_audit(db, audit.id, fix_id)
    if not fix:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fix not found")
    return _fix_response(fix)


@content_router.patch(
    "/runs/{run_id}/posts/{audit_id}/fixes/{fix_id}",
    response_model=BlogContentAuditFixResponse,
)
async def edit_content_audit_fix(
    run_id: int,
    audit_id: int,
    fix_id: int,
    body: BlogContentAuditFixEditRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.blog_content_audit_fix_service import edit_fix

    try:
        fix = edit_fix(
            db,
            run_id=run_id,
            audit_id=audit_id,
            fix_id=fix_id,
            user_id=current_user.id,
            after_proposed=body.after_proposed,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fix not found")
    return _fix_response(fix)


@content_router.post(
    "/runs/{run_id}/posts/{audit_id}/fixes/{fix_id}/regenerate",
    response_model=BlogContentAuditFixResponse,
)
async def regenerate_content_audit_fix(
    run_id: int,
    audit_id: int,
    fix_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.blog_content_audit_fix_service import regenerate_fix

    try:
        fix = await regenerate_fix(
            db,
            run_id=run_id,
            audit_id=audit_id,
            fix_id=fix_id,
            user_id=current_user.id,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fix not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _fix_response(fix)


@content_router.post(
    "/runs/{run_id}/posts/{audit_id}/fixes/{fix_id}/skip",
    response_model=BlogContentAuditFixResponse,
)
async def skip_content_audit_fix(
    run_id: int,
    audit_id: int,
    fix_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.blog_content_audit_fix_service import skip_fix

    try:
        fix = skip_fix(
            db,
            run_id=run_id,
            audit_id=audit_id,
            fix_id=fix_id,
            user_id=current_user.id,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fix not found")
    return _fix_response(fix)


@content_router.post(
    "/runs/{run_id}/posts/{audit_id}/fixes/preview-combined",
    response_model=BlogContentAuditFixPreviewCombinedResponse,
)
async def preview_combined_content_audit_fixes(
    run_id: int,
    audit_id: int,
    body: BlogContentAuditFixPreviewCombinedRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.services.blog_content_audit_fix_service import preview_combined

    try:
        result = preview_combined(
            db,
            run_id=run_id,
            audit_id=audit_id,
            user_id=current_user.id,
            fix_ids=body.fix_ids,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post audit not found")
    return BlogContentAuditFixPreviewCombinedResponse(**result)


@content_router.get("/sites/{wordpress_site_id}/latest", response_model=BlogContentAuditRunResponse)
async def get_latest_content_audit_for_site(
    wordpress_site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_user_site(db, current_user.id, wordpress_site_id)
    run = get_active_or_latest_content_audit(db, wordpress_site_id, current_user.id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No content audit found")
    return _content_run_response(run)


router.include_router(content_router)
