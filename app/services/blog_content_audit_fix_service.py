"""Orchestrate blog content audit fix generation and preview."""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.blog_content_audit import BlogContentAudit
from app.models.blog_content_audit_fix import BlogContentAuditFix
from app.models.blog_content_audit_run import BlogContentAuditRun
from app.models.wordpress_site import WordPressSite
from app.services.blog_content_audit_fix_generators import (
    append_sources_footer,
    fix_image_alt_html,
    generate_fix_proposal,
)
from app.services.blog_content_audit_fix_planner import (
    CONTENT_FIX_ORDER,
    META_FIX_TYPES,
    plan_fixes,
)
from app.services.blog_content_audit_parser import parse_post_content
from app.services.humanizer.diff import build_diff
from app.services.wordpress_service import fetch_wp_content_for_audit

logger = logging.getLogger(__name__)


def get_audit_for_user(
    db: Session, run_id: int, audit_id: int, user_id: int
) -> Optional[BlogContentAudit]:
    return (
        db.query(BlogContentAudit)
        .join(BlogContentAuditRun, BlogContentAudit.run_id == BlogContentAuditRun.id)
        .filter(
            BlogContentAudit.id == audit_id,
            BlogContentAudit.run_id == run_id,
            BlogContentAuditRun.user_id == user_id,
        )
        .first()
    )


def get_fix_for_audit(
    db: Session, audit_id: int, fix_id: int
) -> Optional[BlogContentAuditFix]:
    return (
        db.query(BlogContentAuditFix)
        .filter(BlogContentAuditFix.audit_id == audit_id, BlogContentAuditFix.id == fix_id)
        .first()
    )


async def _load_post_snapshot(
    db: Session, audit: BlogContentAudit, user_id: int
) -> dict[str, Any]:
    run = db.query(BlogContentAuditRun).filter(BlogContentAuditRun.id == audit.run_id).first()
    if not run:
        raise ValueError("Audit run not found")
    site = (
        db.query(WordPressSite)
        .filter(WordPressSite.id == run.wordpress_site_id, WordPressSite.user_id == user_id)
        .first()
    )
    if not site:
        raise ValueError("WordPress site not found")
    if not audit.wp_post_id:
        raise ValueError("Post has no WordPress ID")

    wp = await fetch_wp_content_for_audit(site, int(audit.wp_post_id), "post")
    if not wp.get("success"):
        raise ValueError(wp.get("message") or "Failed to fetch post content")

    html = wp.get("content_html") or ""
    title = (wp.get("title") or audit.title or "").strip()
    meta = (wp.get("meta_description") or "").strip() or None
    parsed = parse_post_content(html=html, title=title, url=audit.url, meta_description=meta)
    return {
        "content_html": html,
        "title": title,
        "meta_description": meta,
        "parsed": parsed,
    }


def _fix_to_dict(fix: BlogContentAuditFix) -> dict[str, Any]:
    return {
        "id": fix.id,
        "audit_id": fix.audit_id,
        "fix_type": fix.fix_type,
        "target_field": fix.target_field,
        "title": fix.title,
        "description": fix.description,
        "severity": fix.severity,
        "status": fix.status,
        "finding_refs": fix.finding_refs or [],
        "before_text": fix.before_text,
        "after_proposed": fix.after_proposed,
        "diff_segments": fix.diff_segments or [],
        "generator_meta": fix.generator_meta,
        "created_at": fix.created_at,
        "updated_at": fix.updated_at,
    }


async def generate_fixes(
    db: Session,
    *,
    run_id: int,
    audit_id: int,
    user_id: int,
    force: bool = False,
) -> list[BlogContentAuditFix]:
    audit = get_audit_for_user(db, run_id, audit_id, user_id)
    if not audit:
        raise LookupError("Post audit not found")

    existing = db.query(BlogContentAuditFix).filter(BlogContentAuditFix.audit_id == audit.id).all()
    if existing and not force:
        return existing

    if existing and force:
        for f in existing:
            db.delete(f)
        db.flush()

    snapshot = await _load_post_snapshot(db, audit, user_id)
    parsed = snapshot["parsed"]
    content_html = snapshot["content_html"]
    meta = snapshot["meta_description"]

    plans = plan_fixes(
        findings=audit.findings if isinstance(audit.findings, list) else [],
        gap_matrix=audit.gap_matrix if isinstance(audit.gap_matrix, list) else [],
        adsense_risks=audit.adsense_risks if isinstance(audit.adsense_risks, list) else [],
        audit_payload=audit.audit_payload if isinstance(audit.audit_payload, dict) else {},
        word_target_met=bool(audit.word_target_met),
        word_count=audit.word_count,
        word_target=audit.word_target,
        article_type=audit.article_type,
        has_meta=bool(meta and meta.strip()),
        meta_length=len(meta or ""),
        missing_alt_count=parsed.images_missing_alt,
        has_sources_footer=parsed.has_last_updated_footer,
    )

    created: list[BlogContentAuditFix] = []
    for plan in plans:
        try:
            proposal = await generate_fix_proposal(
                plan,
                title=snapshot["title"],
                article_type=audit.article_type,
                content_html=content_html,
                meta_description=meta,
            )
        except Exception as exc:
            logger.warning("Fix generation failed for %s: %s", plan.fix_type, exc)
            continue

        fix = BlogContentAuditFix(
            audit_id=audit.id,
            fix_type=plan.fix_type,
            target_field=plan.target_field,
            title=plan.title,
            description=plan.description,
            severity=plan.severity,
            status="proposed",
            finding_refs=plan.finding_refs,
            before_text=proposal["before_text"],
            after_proposed=proposal["after_proposed"],
            diff_segments=proposal["diff_segments"],
            generator_meta=proposal["generator_meta"],
        )
        db.add(fix)
        created.append(fix)

    db.commit()
    for f in created:
        db.refresh(f)
    return created


async def regenerate_fix(
    db: Session,
    *,
    run_id: int,
    audit_id: int,
    fix_id: int,
    user_id: int,
) -> BlogContentAuditFix:
    audit = get_audit_for_user(db, run_id, audit_id, user_id)
    if not audit:
        raise LookupError("Post audit not found")
    fix = get_fix_for_audit(db, audit.id, fix_id)
    if not fix:
        raise LookupError("Fix not found")

    snapshot = await _load_post_snapshot(db, audit, user_id)
    from app.services.blog_content_audit_fix_planner import FixPlanItem

    plan = FixPlanItem(
        fix_type=fix.fix_type,
        target_field=fix.target_field,
        title=fix.title,
        description=fix.description or "",
        severity=fix.severity,
        finding_refs=fix.finding_refs or [],
        payload=_payload_for_fix(fix, audit),
    )
    proposal = await generate_fix_proposal(
        plan,
        title=snapshot["title"],
        article_type=audit.article_type,
        content_html=snapshot["content_html"],
        meta_description=snapshot["meta_description"],
    )
    meta = fix.generator_meta or {}
    meta["regen_count"] = int(meta.get("regen_count") or 0) + 1
    fix.before_text = proposal["before_text"]
    fix.after_proposed = proposal["after_proposed"]
    fix.diff_segments = proposal["diff_segments"]
    fix.generator_meta = meta
    fix.status = "proposed"
    db.commit()
    db.refresh(fix)
    return fix


def _payload_for_fix(fix: BlogContentAuditFix, audit: BlogContentAudit) -> dict[str, Any]:
    if fix.fix_type == "add_missing_sections":
        gaps = audit.gap_matrix or []
        topics = [g.get("topic") for g in gaps if isinstance(g, dict) and g.get("status") == "missing"]
        return {"topics": topics[:3]}
    if fix.fix_type == "add_faq_section":
        payload = audit.audit_payload or {}
        return {"topics": payload.get("recommended_topics") or []}
    if fix.fix_type == "expand_content":
        return {"word_target": audit.word_target}
    if fix.fix_type == "fix_image_alt":
        return {"max_images": 5}
    return {}


def edit_fix(
    db: Session,
    *,
    run_id: int,
    audit_id: int,
    fix_id: int,
    user_id: int,
    after_proposed: str,
) -> BlogContentAuditFix:
    audit = get_audit_for_user(db, run_id, audit_id, user_id)
    if not audit:
        raise LookupError("Post audit not found")
    fix = get_fix_for_audit(db, audit.id, fix_id)
    if not fix:
        raise LookupError("Fix not found")

    before = fix.before_text or ""
    fix.after_proposed = after_proposed
    fix.diff_segments = [s.model_dump() for s in build_diff(before, after_proposed)]
    fix.status = "edited"
    db.commit()
    db.refresh(fix)
    return fix


def skip_fix(
    db: Session,
    *,
    run_id: int,
    audit_id: int,
    fix_id: int,
    user_id: int,
) -> BlogContentAuditFix:
    audit = get_audit_for_user(db, run_id, audit_id, user_id)
    if not audit:
        raise LookupError("Post audit not found")
    fix = get_fix_for_audit(db, audit.id, fix_id)
    if not fix:
        raise LookupError("Fix not found")
    fix.status = "skipped"
    db.commit()
    db.refresh(fix)
    return fix


def _strip_html_words(html: str) -> int:
    import re

    text = re.sub(r"<[^>]+>", " ", html or "")
    return len(re.sub(r"\s+", " ", text).strip().split())


def _apply_content_fix(working: str, fix: BlogContentAuditFix) -> str:
    before = fix.before_text or ""
    after = fix.after_proposed or working
    if fix.fix_type == "append_sources_footer":
        return append_sources_footer(working)
    if fix.fix_type == "fix_image_alt":
        return fix_image_alt_html(working, max_images=5)
    if fix.fix_type == "expand_content":
        return after
    if before and after.startswith(before):
        return working + after[len(before) :]
    if after != before:
        return working.rstrip() + "\n" + after
    return working


def preview_combined(
    db: Session,
    *,
    run_id: int,
    audit_id: int,
    user_id: int,
    fix_ids: list[int],
) -> dict[str, Any]:
    audit = get_audit_for_user(db, run_id, audit_id, user_id)
    if not audit:
        raise LookupError("Post audit not found")

    fixes = (
        db.query(BlogContentAuditFix)
        .filter(
            BlogContentAuditFix.audit_id == audit.id,
            BlogContentAuditFix.id.in_(fix_ids),
            BlogContentAuditFix.status != "skipped",
        )
        .all()
    )
    if not fix_ids:
        fixes = (
            db.query(BlogContentAuditFix)
            .filter(
                BlogContentAuditFix.audit_id == audit.id,
                BlogContentAuditFix.status != "skipped",
            )
            .all()
        )

    fix_by_id = {f.id: f for f in fixes}
    ordered = [fix_by_id[fid] for fid in fix_ids if fid in fix_by_id] if fix_ids else fixes

    # Sort content fixes by merge order
    def sort_key(f: BlogContentAuditFix) -> int:
        if f.fix_type in META_FIX_TYPES:
            return 100
        try:
            return CONTENT_FIX_ORDER.index(f.fix_type)
        except ValueError:
            return 50

    ordered = sorted(ordered, key=sort_key)

    original_content = ""
    original_meta = ""
    for f in fixes:
        if f.target_field == "content" and f.before_text:
            original_content = f.before_text
            break
    for f in fixes:
        if f.target_field == "meta" and f.before_text is not None:
            original_meta = f.before_text or ""
            break

    merged_content = original_content
    merged_meta = original_meta

    for f in ordered:
        if f.status == "skipped" or not f.after_proposed:
            continue
        if f.target_field == "meta":
            merged_meta = f.after_proposed
        elif f.target_field == "content":
            merged_content = _apply_content_fix(merged_content, f)

    wc_before = _strip_html_words(original_content)
    wc_after = _strip_html_words(merged_content)
    diff = [s.model_dump() for s in build_diff(original_content, merged_content)]

    return {
        "merged_content": merged_content,
        "merged_meta": merged_meta or None,
        "diff_segments": diff,
        "word_count_before": wc_before,
        "word_count_after": wc_after,
    }
