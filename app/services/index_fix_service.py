import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.gsc_property import GscProperty
from app.models.gsc_url_inspection import GscUrlInspection
from app.models.seo_action_item import SeoActionItem
from app.models.workflow_state import WorkflowState
from app.models.wordpress_site import WordPressSite
from app.services.gsc_oauth_service import get_valid_gsc_access_token
from app.services.gsc_sitemap_service import (
    build_gsc_inspect_url,
    sitemap_feedpath,
    submit_sitemap,
)
from app.services.gsc_url_inspection_service import inspect_url, is_not_indexed
from app.services.openrouter_service import generate_content, generate_meta_description
from app.services.wordpress_service import (
    _markdown_to_html,
    fetch_wp_sitemap_url,
    get_wp_post,
    update_wp_post,
)

logger = logging.getLogger(__name__)

MANUAL_ONLY_CAUSES = frozenset({"robots_blocked", "quality_issue", "unknown"})
SAFE_AUTO_FIXES = frozenset({
    "clear_noindex",
    "update_meta_description",
    "publish_post",
    "set_canonical",
    "expand_content",
})
SITEMAP_DEDUPE_HOURS = 24


@dataclass
class FixPlan:
    fix_type: str
    description: str
    payload: dict[str, Any] = field(default_factory=dict)


def _snapshot(action: SeoActionItem) -> dict[str, Any]:
    return action.metric_snapshot if isinstance(action.metric_snapshot, dict) else {}


def _diagnosis(action: SeoActionItem) -> dict[str, Any]:
    diag = _snapshot(action).get("diagnosis") or {}
    return diag if isinstance(diag, dict) else {}


def _live_signals(action: SeoActionItem) -> dict[str, Any]:
    live = _snapshot(action).get("live_signals") or {}
    return live if isinstance(live, dict) else {}


def _meta_description_from_post(wp_post: dict[str, Any]) -> str:
    meta = wp_post.get("meta") or {}
    return str(meta.get("rank_math_description") or "").strip()


def _workflow_body(workflow: Optional[WorkflowState], published_post_id: Optional[int]) -> tuple[str, str]:
    """Return (title, body) from workflow state for content expansion."""
    if not workflow or not isinstance(workflow.state_data, dict):
        return "", ""
    data = workflow.state_data
    title = str(data.get("title") or data.get("suggested_title") or data.get("topic") or "")
    body = str(data.get("body_content") or "")
    if body:
        return title, body

    parent = data.get("parent") or {}
    if isinstance(parent, dict) and parent.get("body_content"):
        return str(parent.get("title") or title), str(parent.get("body_content") or "")

    for child in data.get("children") or []:
        if isinstance(child, dict) and child.get("body_content"):
            return str(child.get("title") or title), str(child.get("body_content") or "")

    return title, body


def plan_fixes(action: SeoActionItem, wp_post: Optional[dict[str, Any]] = None) -> list[FixPlan]:
    """Determine which fixes can be auto-applied for a not_indexed action."""
    diag = _diagnosis(action)
    live = _live_signals(action)
    snap = _snapshot(action)
    root_cause = str(diag.get("root_cause") or "").lower()
    indexing_state = str(snap.get("indexing_state") or "").upper()

    if root_cause in MANUAL_ONLY_CAUSES:
        return []

    fixes: list[FixPlan] = []

    if live.get("has_noindex") or indexing_state in ("BLOCKED_BY_META_TAG", "BLOCKED_BY_HTTP_HEADER"):
        fixes.append(FixPlan("clear_noindex", "Remove noindex so Google can index the page."))

    if wp_post and wp_post.get("success"):
        status = str(wp_post.get("status") or "").lower()
        if status == "draft":
            fixes.append(FixPlan("publish_post", "Publish the WordPress post."))

        meta_desc = _meta_description_from_post(wp_post) or str(live.get("meta_description") or "").strip()
        if len(meta_desc) < 80:
            fixes.append(FixPlan(
                "update_meta_description",
                "Generate and set an SEO meta description.",
                {"topic": wp_post.get("title") or snap.get("title") or action.page or ""},
            ))

        google_canonical = str(snap.get("google_canonical") or "").strip()
        user_canonical = str(snap.get("user_canonical") or live.get("canonical") or "").strip()
        if google_canonical and user_canonical and google_canonical.rstrip("/") != user_canonical.rstrip("/"):
            fixes.append(FixPlan(
                "set_canonical",
                "Align canonical URL with the preferred page URL.",
                {"canonical": user_canonical or action.page},
            ))

    if root_cause == "thin_content" and action.linked_workflow_id:
        fixes.append(FixPlan("expand_content", "Expand thin content from linked workflow."))

    # Deduplicate by fix_type preserving order
    seen: set[str] = set()
    unique: list[FixPlan] = []
    for fix in fixes:
        if fix.fix_type in SAFE_AUTO_FIXES and fix.fix_type not in seen:
            seen.add(fix.fix_type)
            unique.append(fix)
    return unique


async def _expand_content_body(title: str, body: str, target_words: int = 500) -> str:
    prompt = f"""Expand the following article body by approximately {target_words} words.
Keep the same topic, tone, and facts. Add useful sections or detail — do not change the title.
Return ONLY the full expanded body (markdown or HTML), no commentary.

Title: {title[:200]}

Current body:
{body[:12000]}
"""
    expanded = await generate_content(prompt, max_tokens=2500, temperature=0.5)
    return expanded.strip()


def _upsert_inspection(db: Session, property_id: int, url: str, result: dict[str, Any]) -> None:
    existing = (
        db.query(GscUrlInspection)
        .filter(GscUrlInspection.property_id == property_id, GscUrlInspection.url == url)
        .first()
    )
    now = datetime.now(timezone.utc)
    if existing:
        existing.verdict = result.get("verdict")
        existing.coverage_state = result.get("coverage_state")
        existing.indexing_state = result.get("indexing_state")
        existing.robots_txt_state = result.get("robots_txt_state")
        existing.page_fetch_state = result.get("page_fetch_state")
        existing.google_canonical = result.get("google_canonical")
        existing.user_canonical = result.get("user_canonical")
        existing.last_crawl_time = result.get("last_crawl_time")
        existing.raw_response = result.get("raw_response")
        existing.inspected_at = now
    else:
        db.add(GscUrlInspection(
            property_id=property_id,
            url=url,
            verdict=result.get("verdict"),
            coverage_state=result.get("coverage_state"),
            indexing_state=result.get("indexing_state"),
            robots_txt_state=result.get("robots_txt_state"),
            page_fetch_state=result.get("page_fetch_state"),
            google_canonical=result.get("google_canonical"),
            user_canonical=result.get("user_canonical"),
            last_crawl_time=result.get("last_crawl_time"),
            raw_response=result.get("raw_response"),
            inspected_at=now,
        ))


async def _maybe_submit_sitemap(
    db: Session,
    prop: GscProperty,
    access_token: str,
    wp_site: WordPressSite,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if prop.last_sitemap_submitted_at:
        last = prop.last_sitemap_submitted_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last < timedelta(hours=SITEMAP_DEDUPE_HOURS):
            return {"success": True, "skipped": True, "message": "Sitemap recently submitted"}

    sitemap_url = await fetch_wp_sitemap_url(wp_site)
    if not sitemap_url:
        return {"success": False, "message": "Could not discover sitemap URL"}

    feedpath = sitemap_feedpath(sitemap_url)
    result = await submit_sitemap(access_token, prop.site_url, feedpath)
    if result.get("success"):
        prop.last_sitemap_submitted_at = now
        db.commit()
    return result


async def apply_index_fixes(db: Session, user_id: int, action_id: int) -> None:
    """Apply planned fixes for a not_indexed action, then queue manual indexing."""
    action = (
        db.query(SeoActionItem)
        .filter(SeoActionItem.id == action_id, SeoActionItem.user_id == user_id)
        .first()
    )
    if not action or action.action_type != "not_indexed":
        return

    snap = _snapshot(action)
    outcomes: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        prop = (
            db.query(GscProperty)
            .filter(GscProperty.id == action.property_id, GscProperty.user_id == user_id)
            .first()
        )
        if not prop or not prop.wordpress_site_id:
            raise ValueError("GSC property or linked WordPress site not found")

        wp_site = (
            db.query(WordPressSite)
            .filter(WordPressSite.id == prop.wordpress_site_id, WordPressSite.user_id == user_id)
            .first()
        )
        if not wp_site:
            raise ValueError("Linked WordPress site not found")

        wp_post_id = snap.get("wp_post_id")
        wp_post_type = snap.get("wp_post_type") or "post"
        wp_post: dict[str, Any] = {"success": False}
        if wp_post_id:
            wp_post = await get_wp_post(wp_site, int(wp_post_id), str(wp_post_type))

        plans = plan_fixes(action, wp_post)
        root_cause = str(_diagnosis(action).get("root_cause") or "").lower()

        if not plans and root_cause in MANUAL_ONLY_CAUSES:
            action.fix_status = "manual_required"
            action.indexing_status = "ready_for_manual"
            action.gsc_inspect_url = build_gsc_inspect_url(prop.site_url, action.page or "")
            action.fix_result = {"outcomes": [], "errors": ["Manual fix required for this issue type."]}
            action.fixed_at = datetime.now(timezone.utc)
            action.status = "approved"
            db.commit()
            return

        if not plans:
            action.fix_status = "skipped"
            action.fix_result = {"outcomes": [], "errors": ["No automatic fixes available."]}
            action.indexing_status = "ready_for_manual"
            action.gsc_inspect_url = build_gsc_inspect_url(prop.site_url, action.page or "")
            action.fixed_at = datetime.now(timezone.utc)
            action.status = "approved"
            db.commit()
            return

        workflow = None
        if action.linked_workflow_id:
            workflow = db.query(WorkflowState).filter(WorkflowState.id == action.linked_workflow_id).first()

        for plan in plans:
            try:
                if plan.fix_type == "clear_noindex" and wp_post_id:
                    result = await update_wp_post(
                        wp_site,
                        int(wp_post_id),
                        str(wp_post_type),
                        {"meta": {"rank_math_robots": ["index"], "rank_math_advanced_robots": []}},
                    )
                    outcomes.append({"fix": plan.fix_type, "success": result.get("success"), "detail": result})

                elif plan.fix_type == "publish_post" and wp_post_id:
                    result = await update_wp_post(
                        wp_site,
                        int(wp_post_id),
                        str(wp_post_type),
                        {"status": "publish"},
                    )
                    outcomes.append({"fix": plan.fix_type, "success": result.get("success"), "detail": result})

                elif plan.fix_type == "update_meta_description" and wp_post_id:
                    topic = str(plan.payload.get("topic") or action.page or "page")
                    meta = await generate_meta_description(topic=topic, keywords=[])
                    result = await update_wp_post(
                        wp_site,
                        int(wp_post_id),
                        str(wp_post_type),
                        {"meta": {"rank_math_description": meta}},
                    )
                    outcomes.append({"fix": plan.fix_type, "success": result.get("success"), "detail": result})

                elif plan.fix_type == "set_canonical" and wp_post_id:
                    canonical = str(plan.payload.get("canonical") or action.page or "")
                    result = await update_wp_post(
                        wp_site,
                        int(wp_post_id),
                        str(wp_post_type),
                        {"meta": {"rank_math_canonical_url": canonical}},
                    )
                    outcomes.append({"fix": plan.fix_type, "success": result.get("success"), "detail": result})

                elif plan.fix_type == "expand_content" and wp_post_id and workflow:
                    title, body = _workflow_body(workflow, action.linked_published_post_id)
                    if not body:
                        outcomes.append({"fix": plan.fix_type, "success": False, "detail": "No workflow body found"})
                    else:
                        expanded = await _expand_content_body(title, body)
                        html = _markdown_to_html(expanded)
                        result = await update_wp_post(
                            wp_site,
                            int(wp_post_id),
                            str(wp_post_type),
                            {"content": html},
                        )
                        outcomes.append({"fix": plan.fix_type, "success": result.get("success"), "detail": result})
                else:
                    outcomes.append({"fix": plan.fix_type, "success": False, "detail": "Missing prerequisites"})
            except Exception as exc:
                logger.exception("Fix %s failed for action %s", plan.fix_type, action_id)
                errors.append(f"{plan.fix_type}: {exc}")
                outcomes.append({"fix": plan.fix_type, "success": False, "error": str(exc)})

        any_success = any(o.get("success") for o in outcomes)
        access_token = await get_valid_gsc_access_token(db, user_id)

        sitemap_result: dict[str, Any] = {"skipped": True}
        if any_success:
            sitemap_result = await _maybe_submit_sitemap(db, prop, access_token, wp_site)
            if sitemap_result.get("needs_reconnect"):
                errors.append(sitemap_result.get("message") or "GSC reconnect required for sitemap submit")

        if action.page:
            inspection = await inspect_url(access_token, prop.site_url, action.page)
            _upsert_inspection(db, prop.id, action.page, inspection)
            snap = dict(snap)
            snap["post_fix_inspection"] = {
                "verdict": inspection.get("verdict"),
                "coverage_state": inspection.get("coverage_state"),
                "indexing_state": inspection.get("indexing_state"),
            }
            action.metric_snapshot = snap

            if not is_not_indexed(inspection):
                action.indexing_status = "verified_indexed"
            elif sitemap_result.get("success"):
                action.indexing_status = "sitemap_submitted"
            else:
                action.indexing_status = "ready_for_manual"

            action.gsc_inspect_url = build_gsc_inspect_url(prop.site_url, action.page)

        if errors and not any_success:
            action.fix_status = "failed"
        elif any_success:
            action.fix_status = "applied"
        else:
            action.fix_status = "failed"

        action.fix_result = {
            "outcomes": outcomes,
            "errors": errors,
            "sitemap": sitemap_result,
            "needs_reconnect": bool(sitemap_result.get("needs_reconnect")),
        }
        snap = _snapshot(action)
        snap["fix_execution"] = action.fix_result
        action.metric_snapshot = snap
        action.fixed_at = datetime.now(timezone.utc)
        action.status = "approved"
        db.commit()

    except Exception as exc:
        logger.exception("apply_index_fixes failed for action %s", action_id)
        action.fix_status = "failed"
        action.fix_result = {"outcomes": outcomes, "errors": errors + [str(exc)]}
        action.fixed_at = datetime.now(timezone.utc)
        action.status = "approved"
        db.commit()


async def bg_apply_index_fix(action_id: int, user_id: int) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        await apply_index_fixes(db, user_id, action_id)
    finally:
        db.close()
