"""Website Audit orchestrator — BackgroundTasks + DB run status."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.website_audit_issue import WebsiteAuditIssue
from app.models.website_audit_page import WebsiteAuditPage
from app.models.website_audit_run import WebsiteAuditRun
from app.models.wordpress_site import WordPressSite
from app.services.page_fetch_service import (
    fetch_page_signals,
    is_blocked_status,
    signals_from_html,
)
from app.services.website_audit_checks import analyze_page, analyze_site_duplicates
from app.services.website_audit_discover import DEFAULT_MAX_PAGES, HARD_MAX_PAGES, discover_audit_urls
from app.services.website_audit_scoring import compute_health_scores
from app.services.wordpress_service import fetch_wp_content_for_audit

logger = logging.getLogger(__name__)

FETCH_CONCURRENCY = 3
CHUNK_SIZE = 8
CHUNK_PAUSE_SECONDS = 0.75
BLOCKED_BURST_THRESHOLD = 3  # consecutive blocked public fetches → WP-only mode
REQUEST_GAP_SECONDS = 0.35


def get_latest_audit_run(
    db: Session, wordpress_site_id: int, user_id: int
) -> Optional[WebsiteAuditRun]:
    return (
        db.query(WebsiteAuditRun)
        .filter(
            WebsiteAuditRun.wordpress_site_id == wordpress_site_id,
            WebsiteAuditRun.user_id == user_id,
        )
        .order_by(WebsiteAuditRun.started_at.desc())
        .first()
    )


def has_running_audit(db: Session, wordpress_site_id: int, user_id: int) -> bool:
    latest = get_latest_audit_run(db, wordpress_site_id, user_id)
    return latest is not None and latest.status == "running"


def get_run_for_user(db: Session, run_id: int, user_id: int) -> Optional[WebsiteAuditRun]:
    return (
        db.query(WebsiteAuditRun)
        .filter(WebsiteAuditRun.id == run_id, WebsiteAuditRun.user_id == user_id)
        .first()
    )


def _needs_wp_fallback(signals: dict[str, Any]) -> bool:
    status = signals.get("status_code")
    fetch_status = signals.get("fetch_status")
    if fetch_status in ("fetch_blocked", "timeout", "not_html", "too_large"):
        return True
    if status is not None and is_blocked_status(int(status)):
        return True
    if signals.get("fetch_error") and not (signals.get("word_count") or 0):
        return True
    return False


async def _apply_wp_fallback(
    site: WordPressSite,
    item: dict[str, Any],
    public_signals: dict[str, Any],
) -> dict[str, Any]:
    wp_post_id = item.get("wp_post_id")
    if not wp_post_id:
        return public_signals

    post_type = item.get("post_type") or "post"
    url = item["url"]
    try:
        wp = await fetch_wp_content_for_audit(site, int(wp_post_id), post_type)
    except Exception as exc:
        logger.warning("WP audit fallback failed for %s: %s", url, exc)
        return public_signals

    if not wp.get("success"):
        return public_signals

    html = wp.get("content_html") or ""
    title = (wp.get("title") or "").strip() or None
    meta = (wp.get("meta_description") or "").strip() or None
    if not html and not title:
        return public_signals

    merged = signals_from_html(
        html or f"<p>{title or ''}</p>",
        url,
        source="wordpress_rest_fallback",
        status_code=200,
        title_override=title,
        meta_override=meta,
    )
    # Preserve public block evidence
    merged["public_status_code"] = public_signals.get("status_code")
    merged["public_fetch_error"] = public_signals.get("fetch_error")
    merged["public_fetch_status"] = public_signals.get("fetch_status")
    if _needs_wp_fallback(public_signals):
        merged["fetch_status"] = "fetch_blocked"
        merged["status_code"] = public_signals.get("status_code") or 403
        merged["fetch_error"] = public_signals.get("fetch_error")
    return merged


async def _fetch_item_signals(
    site: WordPressSite,
    item: dict[str, Any],
    *,
    wp_only: bool,
    referer: str,
) -> dict[str, Any]:
    url = item["url"]
    if wp_only and item.get("wp_post_id"):
        empty = {
            "url": url,
            "status_code": 403,
            "fetch_error": "http_403",
            "fetch_status": "fetch_blocked",
            "fetch_source": "public_html",
            "word_count": 0,
            "title": None,
            "meta_description": None,
        }
        return await _apply_wp_fallback(site, item, empty)

    signals = await fetch_page_signals(url, referer=referer)
    if _needs_wp_fallback(signals) and item.get("wp_post_id"):
        signals = await _apply_wp_fallback(site, item, signals)
    return signals


async def run_website_audit(db: Session, user_id: int, run_id: int) -> None:
    audit_run = (
        db.query(WebsiteAuditRun)
        .filter(WebsiteAuditRun.id == run_id, WebsiteAuditRun.user_id == user_id)
        .first()
    )
    if not audit_run:
        return

    try:
        site = (
            db.query(WordPressSite)
            .filter(
                WordPressSite.id == audit_run.wordpress_site_id,
                WordPressSite.user_id == user_id,
            )
            .first()
        )
        if not site:
            raise ValueError("WordPress site not found")

        scope = dict(audit_run.scope_json or {})
        max_pages = int(scope.get("max_pages") or DEFAULT_MAX_PAGES)
        max_pages = max(1, min(max_pages, HARD_MAX_PAGES))
        content_types = scope.get("content_types")
        concurrency = int(scope.get("concurrency") or FETCH_CONCURRENCY)
        concurrency = max(1, min(concurrency, 5))

        audit_run.status = "running"
        audit_run.stage = "discover"
        db.commit()

        urls = await discover_audit_urls(
            site, max_pages=max_pages, content_types=content_types
        )
        audit_run.total_urls = len(urls)
        audit_run.processed_urls = 0
        audit_run.stage = "fetch"
        db.commit()

        page_rows: list[WebsiteAuditPage] = []
        all_issues: list[dict[str, Any]] = []
        page_meta_for_dupes: list[dict[str, Any]] = []

        pages_fetched_ok = 0
        pages_fetch_failed = 0
        pages_from_wp_fallback = 0
        pages_analyzed = 0
        consecutive_blocked = 0
        wp_only_mode = False
        crawl_notes: list[str] = []

        referer = site.url if site.url.endswith("/") else site.url.rstrip("/") + "/"
        host = urlparse(site.url).netloc
        sem = asyncio.Semaphore(concurrency)
        gap_lock = asyncio.Lock()
        last_request_at = 0.0

        async def _throttled_fetch(item: dict[str, Any]) -> dict[str, Any]:
            nonlocal last_request_at
            async with sem:
                async with gap_lock:
                    loop = asyncio.get_event_loop()
                    now = loop.time()
                    wait = REQUEST_GAP_SECONDS - (now - last_request_at)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    last_request_at = loop.time()
                signals = await _fetch_item_signals(
                    site, item, wp_only=wp_only_mode, referer=referer
                )
                return {"item": item, "signals": signals}

        for i in range(0, len(urls), CHUNK_SIZE):
            chunk = urls[i : i + CHUNK_SIZE]
            # Sequential within chunk when wp_only to be gentle on REST; else limited parallel
            if wp_only_mode:
                results = []
                for item in chunk:
                    results.append(await _throttled_fetch(item))
            else:
                results = await asyncio.gather(*[_throttled_fetch(item) for item in chunk])

            for result in results:
                item = result["item"]
                signals = result["signals"]
                url = item["url"]
                post_type = item.get("post_type") or "post"
                source = signals.get("fetch_source") or "public_html"
                public_blocked = (
                    signals.get("public_fetch_status") == "fetch_blocked"
                    or (
                        source == "wordpress_rest_fallback"
                        and is_blocked_status(signals.get("public_status_code"))
                    )
                    or (
                        source == "public_html"
                        and signals.get("fetch_status") == "fetch_blocked"
                    )
                )

                if source == "wordpress_rest_fallback":
                    pages_from_wp_fallback += 1
                    pages_fetched_ok += 1
                    consecutive_blocked += 1
                elif signals.get("fetch_status") == "ok":
                    pages_fetched_ok += 1
                    consecutive_blocked = 0
                else:
                    pages_fetch_failed += 1
                    if public_blocked or signals.get("fetch_status") == "fetch_blocked":
                        consecutive_blocked += 1
                    else:
                        consecutive_blocked = 0

                if (
                    not wp_only_mode
                    and consecutive_blocked >= BLOCKED_BURST_THRESHOLD
                ):
                    wp_only_mode = True
                    crawl_notes.append("switched_to_wp_fallback_after_403_burst")
                    logger.info(
                        "Audit %s: switching to WP-only mode after blocked burst",
                        audit_run.id,
                    )

                thin_score, page_issues = analyze_page(
                    signals, url=url, post_type=post_type
                )
                analyzed = (
                    int(signals.get("word_count") or 0) > 0
                    or bool((signals.get("title") or "").strip())
                    or signals.get("fetch_status") == "ok"
                    or source == "wordpress_rest_fallback"
                )
                if analyzed and not (
                    signals.get("fetch_status") in ("http_error",)
                    and source == "public_html"
                    and int(signals.get("word_count") or 0) == 0
                ):
                    # Count as analyzed if we have document or successful public fetch
                    if source == "wordpress_rest_fallback" or signals.get("fetch_status") == "ok":
                        pages_analyzed += 1
                    elif int(signals.get("word_count") or 0) > 0:
                        pages_analyzed += 1

                page = WebsiteAuditPage(
                    run_id=audit_run.id,
                    url=url,
                    wp_post_id=item.get("wp_post_id"),
                    post_type=post_type,
                    status_code=signals.get("status_code"),
                    signals_json=signals,
                    title=signals.get("title"),
                    meta_description=signals.get("meta_description"),
                    word_count=int(signals.get("word_count") or 0),
                    thin_score=thin_score,
                )
                db.add(page)
                db.flush()
                page_rows.append(page)

                for issue in page_issues:
                    all_issues.append({**issue, "page_id": page.id})

                page_meta_for_dupes.append(
                    {
                        "url": url,
                        "page_id": page.id,
                        "title": signals.get("title"),
                        "meta_description": signals.get("meta_description"),
                    }
                )

            audit_run.processed_urls = min(i + len(chunk), len(urls))
            db.commit()
            if i + CHUNK_SIZE < len(urls):
                await asyncio.sleep(CHUNK_PAUSE_SECONDS)

        audit_run.stage = "analyze"
        db.commit()

        for dup in analyze_site_duplicates(page_meta_for_dupes):
            matched = next((p for p in page_meta_for_dupes if p["url"] == dup["url"]), None)
            all_issues.append({**dup, "page_id": matched["page_id"] if matched else None})

        for issue in all_issues:
            db.add(
                WebsiteAuditIssue(
                    run_id=audit_run.id,
                    page_id=issue.get("page_id"),
                    url=issue["url"],
                    category=issue["category"],
                    issue_code=issue["issue_code"],
                    title=issue["title"],
                    severity=issue["severity"],
                    priority_score=float(issue.get("priority_score") or 0),
                    why_it_matters=issue["why_it_matters"],
                    recommendation=issue["recommendation"],
                    evidence=issue.get("evidence"),
                )
            )

        audit_run.stage = "score"
        db.commit()

        health, category_scores = compute_health_scores(
            all_issues,
            pages_audited=len(page_rows),
            pages_fetched_ok=pages_fetched_ok,
            pages_fetch_failed=pages_fetch_failed,
            pages_from_wp_fallback=pages_from_wp_fallback,
            pages_analyzed=pages_analyzed,
        )
        category_scores["crawl"] = {
            "host": host,
            "mode": "wp_only" if wp_only_mode else "hybrid",
            "notes": crawl_notes,
            "user_agent": "TrendifyPressAudit/1.0",
        }
        category_scores["progress"] = {
            "total_urls": len(page_rows),
            "processed_urls": len(page_rows),
            "pages_fetched_ok": pages_fetched_ok,
            "pages_fetch_failed": pages_fetch_failed,
            "pages_from_wp_fallback": pages_from_wp_fallback,
            "pages_analyzed": pages_analyzed,
            "fetch_success_rate": category_scores.get("fetch_success_rate"),
        }

        audit_run.health_score = health
        audit_run.category_scores = category_scores
        audit_run.stage = "completed"
        audit_run.status = "completed"
        audit_run.completed_at = datetime.now(timezone.utc)
        if category_scores.get("warning_message"):
            # Keep warning accessible; error_message reserved for hard failures
            scope["last_warning"] = category_scores["warning_message"]
            audit_run.scope_json = scope
        db.commit()
        logger.info(
            "Website audit %s completed: %s URLs, health=%s, issues=%s, wp_fallback=%s, score_status=%s",
            audit_run.id,
            audit_run.processed_urls,
            health,
            len(all_issues),
            pages_from_wp_fallback,
            category_scores.get("score_status"),
        )
    except Exception as exc:
        logger.exception("Website audit %s failed: %s", run_id, exc)
        audit_run.status = "failed"
        audit_run.error_message = str(exc)
        audit_run.completed_at = datetime.now(timezone.utc)
        db.commit()


async def bg_run_website_audit(user_id: int, run_id: int) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        await run_website_audit(db, user_id, run_id)
    finally:
        db.close()
