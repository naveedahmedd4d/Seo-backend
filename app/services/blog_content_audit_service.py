"""Blog Content Review orchestrator — background run + per-post pipeline."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.blog_content_audit import BlogContentAudit
from app.models.blog_content_audit_run import BlogContentAuditRun
from app.models.website_audit_page import WebsiteAuditPage
from app.models.website_audit_run import WebsiteAuditRun
from app.models.wordpress_site import WordPressSite
from app.services.blog_content_audit_adsense import (
    adsense_quality_score_from_risks,
    compute_site_adsense_risk,
    detect_page_adsense_risks,
)
from app.services.blog_content_audit_config import (
    CHUNK_PAUSE_SECONDS,
    CHUNK_SIZE,
    DEFAULT_POST_LIMIT,
    FETCH_CONCURRENCY,
    HARD_MAX_POSTS,
    REQUEST_GAP_SECONDS,
    SCORE_WEIGHTS,
)
from app.services.blog_content_audit_deterministic import compute_deterministic_audit
from app.services.blog_content_audit_llm import run_llm_content_audit
from app.services.blog_content_audit_parser import parse_post_content
from app.services.blog_content_audit_tavily import (
    build_gap_matrix,
    count_missing_gaps,
    top_missing_topics,
)
from app.services.llm_settings_service import resolve_api_key_optional
from app.services.keyword_coverage_service import slug_from_url

from app.services.wordpress_service import fetch_wp_content_for_audit, list_published_content

logger = logging.getLogger(__name__)


def get_content_audit_run(db: Session, run_id: int, user_id: int) -> Optional[BlogContentAuditRun]:
    return (
        db.query(BlogContentAuditRun)
        .filter(BlogContentAuditRun.id == run_id, BlogContentAuditRun.user_id == user_id)
        .first()
    )


def has_running_content_audit(db: Session, wordpress_site_id: int, user_id: int) -> bool:
    latest = (
        db.query(BlogContentAuditRun)
        .filter(
            BlogContentAuditRun.wordpress_site_id == wordpress_site_id,
            BlogContentAuditRun.user_id == user_id,
        )
        .order_by(BlogContentAuditRun.started_at.desc())
        .first()
    )
    return latest is not None and latest.status == "running"


def get_active_or_latest_content_audit(
    db: Session, wordpress_site_id: int, user_id: int
) -> Optional[BlogContentAuditRun]:
    """Return running/pending run first, else most recent completed."""
    active = (
        db.query(BlogContentAuditRun)
        .filter(
            BlogContentAuditRun.wordpress_site_id == wordpress_site_id,
            BlogContentAuditRun.user_id == user_id,
            BlogContentAuditRun.status.in_(("pending", "running")),
        )
        .order_by(BlogContentAuditRun.started_at.desc())
        .first()
    )
    if active:
        return active
    return get_latest_content_audit(db, wordpress_site_id, user_id)


def get_latest_content_audit(
    db: Session, wordpress_site_id: int, user_id: int
) -> Optional[BlogContentAuditRun]:
    return (
        db.query(BlogContentAuditRun)
        .filter(
            BlogContentAuditRun.wordpress_site_id == wordpress_site_id,
            BlogContentAuditRun.user_id == user_id,
            BlogContentAuditRun.status == "completed",
        )
        .order_by(BlogContentAuditRun.completed_at.desc())
        .first()
    )


async def _discover_posts(
    db: Session,
    site: WordPressSite,
    *,
    website_audit_run_id: Optional[int],
    post_limit: int,
    post_ids: Optional[list[int]],
    user_id: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    if website_audit_run_id:
        wa_run = (
            db.query(WebsiteAuditRun)
            .filter(
                WebsiteAuditRun.id == website_audit_run_id,
                WebsiteAuditRun.user_id == user_id,
                WebsiteAuditRun.wordpress_site_id == site.id,
            )
            .first()
        )
        if wa_run:
            pages = (
                db.query(WebsiteAuditPage)
                .filter(
                    WebsiteAuditPage.run_id == wa_run.id,
                    WebsiteAuditPage.post_type == "post",
                )
                .order_by(WebsiteAuditPage.id.asc())
                .all()
            )
            for p in pages:
                if p.wp_post_id:
                    items.append(
                        {
                            "url": p.url,
                            "wp_post_id": p.wp_post_id,
                            "post_type": "post",
                            "title": p.title,
                        }
                    )

    if not items:
        published = await list_published_content(site, site.url)
        items = [i for i in published if (i.get("wp_post_type") or i.get("post_type") or "post") == "post"]

    if post_ids:
        id_set = set(post_ids)
        items = [i for i in items if i.get("wp_post_id") in id_set]

    return items[:post_limit]


async def _audit_single_post(
    site: WordPressSite,
    item: dict[str, Any],
    *,
    tavily_key: Optional[str],
    site_slugs: list[str],
    skip_llm: bool,
) -> dict[str, Any]:
    wp_post_id = int(item["wp_post_id"])
    post_type = item.get("post_type") or item.get("wp_post_type") or "post"
    url = item["url"]

    wp = await fetch_wp_content_for_audit(site, wp_post_id, post_type)
    if not wp.get("success"):
        raise RuntimeError(wp.get("message") or "Failed to fetch post content")

    title = (wp.get("title") or item.get("title") or "").strip()
    html = wp.get("content_html") or ""
    meta = wp.get("meta_description")

    parsed = parse_post_content(html=html, title=title, url=url, meta_description=meta)
    query = title or parsed.slug.replace("-", " ")

    gap_matrix: list[dict[str, Any]] = []
    if tavily_key:
        gap_matrix = await build_gap_matrix(
            query=query,
            post_text=parsed.plain_text,
            headings=parsed.headings,
            api_key=tavily_key,
        )

    missing_gaps = count_missing_gaps(gap_matrix)
    det = compute_deterministic_audit(parsed, missing_gaps=missing_gaps, site_slugs=site_slugs)

    adsense_risks = detect_page_adsense_risks(
        parsed,
        deterministic_findings=det["findings"],
        gap_missing_count=missing_gaps,
    )
    det["scores"]["adsense_quality"] = adsense_quality_score_from_risks(adsense_risks)

    llm_payload = await run_llm_content_audit(
        parsed,
        deterministic_scores=det["scores"],
        deterministic_findings=det["findings"],
        gap_matrix=gap_matrix,
        adsense_risks=adsense_risks,
        skip_llm=skip_llm,
    )

    scores = llm_payload["scores"]
    word_target_met = llm_payload.get("word_target_met", det["word_target_met"])
    if llm_payload.get("word_target_exception_reason"):
        word_target_met = True

    return {
        "wp_post_id": wp_post_id,
        "url": url,
        "title": title,
        "slug": parsed.slug,
        "article_type": parsed.article_type,
        "word_count": parsed.word_count,
        "word_target": parsed.word_target,
        "word_target_met": word_target_met,
        "seo_score": scores.get("seo"),
        "content_quality_score": scores.get("content_quality"),
        "search_intent_score": scores.get("search_intent"),
        "content_depth_score": scores.get("content_depth"),
        "originality_score": scores.get("originality"),
        "eeat_score": scores.get("eeat"),
        "adsense_quality_score": scores.get("adsense_quality"),
        "overall_score": llm_payload.get("overall_score"),
        "findings": llm_payload.get("findings") or [],
        "gap_matrix": gap_matrix,
        "adsense_risks": adsense_risks,
        "audit_payload": llm_payload,
        "content_hash": parsed.content_hash,
    }


def _compute_site_summary(audits: list[BlogContentAudit]) -> dict[str, Any]:
    if not audits:
        return {
            "avg_scores": {},
            "overall_avg": None,
            "adsense_risk_level": "low",
            "posts_below_word_target": 0,
            "top_gaps": [],
            "posts_audited": 0,
            "posts_failed": 0,
        }

    score_keys = list(SCORE_WEIGHTS.keys())
    avg_scores: dict[str, float] = {}
    for key in score_keys:
        col = {
            "seo": "seo_score",
            "content_quality": "content_quality_score",
            "search_intent": "search_intent_score",
            "content_depth": "content_depth_score",
            "originality": "originality_score",
            "eeat": "eeat_score",
            "adsense_quality": "adsense_quality_score",
        }[key]
        vals = [getattr(a, col) for a in audits if getattr(a, col) is not None]
        if vals:
            avg_scores[key] = round(sum(vals) / len(vals), 1)

    overall_vals = [a.overall_score for a in audits if a.overall_score is not None]
    overall_avg = round(sum(overall_vals) / len(overall_vals), 1) if overall_vals else None

    post_risks = [a.adsense_risks or [] for a in audits]
    adsense_level = compute_site_adsense_risk(post_risks)

    below_target = sum(1 for a in audits if not a.word_target_met)
    gap_counts: dict[str, int] = {}
    for a in audits:
        for g in a.gap_matrix or []:
            if g.get("status") == "missing":
                topic = g.get("topic", "")
                if topic:
                    gap_counts[topic] = gap_counts.get(topic, 0) + 1
    top_gaps = sorted(gap_counts.keys(), key=lambda t: gap_counts[t], reverse=True)[:8]

    return {
        "avg_scores": avg_scores,
        "overall_avg": overall_avg,
        "adsense_risk_level": adsense_level,
        "posts_below_word_target": below_target,
        "top_gaps": top_gaps,
        "posts_audited": len(audits),
        "posts_failed": 0,
    }


async def run_blog_content_audit(db: Session, user_id: int, run_id: int) -> None:
    audit_run = (
        db.query(BlogContentAuditRun)
        .filter(BlogContentAuditRun.id == run_id, BlogContentAuditRun.user_id == user_id)
        .first()
    )
    if not audit_run:
        return

    site = (
        db.query(WordPressSite)
        .filter(
            WordPressSite.id == audit_run.wordpress_site_id,
            WordPressSite.user_id == user_id,
        )
        .first()
    )
    if not site:
        audit_run.status = "failed"
        audit_run.error_message = "WordPress site not found"
        audit_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return

    scope = audit_run.scope_json or {}
    post_limit = min(int(scope.get("post_limit") or DEFAULT_POST_LIMIT), HARD_MAX_POSTS)
    post_ids = scope.get("post_ids")
    skip_llm = bool(scope.get("skip_llm", False))

    audit_run.status = "running"
    db.commit()

    try:
        items = await _discover_posts(
            db,
            site,
            website_audit_run_id=audit_run.website_audit_run_id,
            post_limit=post_limit,
            post_ids=post_ids,
            user_id=user_id,
        )
        site_slugs = [slug_from_url(it.get("url") or "") for it in items]

        tavily_key = resolve_api_key_optional(db, user_id, "tavily")

        total = len(items)
        if total == 0:
            audit_run.status = "failed"
            audit_run.error_message = "No posts found to audit for this site."
            audit_run.completed_at = datetime.now(timezone.utc)
            audit_run.progress_json = {"total": 0, "completed": 0, "failed": 0, "current_post_title": None}
            db.commit()
            return

        completed = 0
        failed = 0
        audit_run.progress_json = {
            "total": total,
            "completed": 0,
            "failed": 0,
            "current_post_title": None,
        }
        db.commit()

        sem = asyncio.Semaphore(FETCH_CONCURRENCY)

        async def process_item(item: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
            async with sem:
                try:
                    result = await _audit_single_post(
                        site,
                        item,
                        tavily_key=tavily_key,
                        site_slugs=site_slugs,
                        skip_llm=skip_llm,
                    )
                    await asyncio.sleep(REQUEST_GAP_SECONDS)
                    return result, None
                except Exception as exc:
                    logger.warning("Content audit failed for %s: %s", item.get("url"), exc)
                    await asyncio.sleep(REQUEST_GAP_SECONDS)
                    return None, str(exc)

        for i in range(0, len(items), CHUNK_SIZE):
            chunk = items[i : i + CHUNK_SIZE]
            title = (chunk[0].get("title") or chunk[0].get("url") or "")[:120]
            audit_run.progress_json = {
                "total": total,
                "completed": completed,
                "failed": failed,
                "current_post_title": title,
            }
            db.commit()

            results = await asyncio.gather(*(process_item(it) for it in chunk))
            for result, err in results:
                if result:
                    db.add(BlogContentAudit(run_id=audit_run.id, **result))
                    completed += 1
                else:
                    failed += 1
            db.commit()

            if i + CHUNK_SIZE < len(items):
                await asyncio.sleep(CHUNK_PAUSE_SECONDS)

        audits = (
            db.query(BlogContentAudit).filter(BlogContentAudit.run_id == audit_run.id).all()
        )
        summary = _compute_site_summary(audits)
        summary["posts_failed"] = failed

        audit_run.site_summary = summary
        audit_run.progress_json = {
            "total": total,
            "completed": completed,
            "failed": failed,
            "current_post_title": None,
        }
        audit_run.status = "completed"
        audit_run.completed_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(
            "Blog content audit %s completed: %s posts, failed=%s, overall_avg=%s",
            audit_run.id,
            completed,
            failed,
            summary.get("overall_avg"),
        )
    except Exception as exc:
        logger.exception("Blog content audit %s failed: %s", run_id, exc)
        audit_run.status = "failed"
        audit_run.error_message = str(exc)
        audit_run.completed_at = datetime.now(timezone.utc)
        db.commit()


async def bg_run_blog_content_audit(user_id: int, run_id: int) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        await run_blog_content_audit(db, user_id, run_id)
    finally:
        db.close()
