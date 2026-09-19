import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session, sessionmaker

from app.models.gsc_index_audit_run import GscIndexAuditRun
from app.models.gsc_property import GscProperty
from app.models.gsc_url_inspection import GscUrlInspection
from app.models.seo_action_item import SeoActionItem
from app.models.wordpress_site import WordPressSite
from app.services.gsc_analysis_service import (
    _build_published_post_map,
    compute_impact_score,
    normalize_page_url,
)
from app.services.gsc_oauth_service import get_valid_gsc_access_token
from app.services.gsc_url_inspection_service import inspect_url, is_not_indexed
from app.services.index_diagnosis_service import diagnose_non_indexed_url
from app.services.page_fetch_service import fetch_page_signals
from app.services.wordpress_service import list_published_content

logger = logging.getLogger(__name__)

CACHE_DAYS = 7
MAX_INSPECTIONS_PER_RUN = 1800
GSC_CALL_DELAY_SECONDS = 0.1


def get_latest_audit_run(db: Session, property_id: int, user_id: int) -> Optional[GscIndexAuditRun]:
    return (
        db.query(GscIndexAuditRun)
        .filter(
            GscIndexAuditRun.property_id == property_id,
            GscIndexAuditRun.user_id == user_id,
        )
        .order_by(GscIndexAuditRun.started_at.desc())
        .first()
    )


def has_running_audit(db: Session, property_id: int, user_id: int) -> bool:
    latest = get_latest_audit_run(db, property_id, user_id)
    return latest is not None and latest.status == "running"


def _severity_for_not_indexed(modified_gmt: Optional[str]) -> str:
    if not modified_gmt:
        return "warning"
    try:
        modified = datetime.fromisoformat(modified_gmt.replace("Z", "+00:00"))
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - modified < timedelta(days=14):
            return "critical"
    except ValueError:
        pass
    return "warning"


def _impact_for_not_indexed(severity: str) -> float:
    impressions = 500 if severity == "critical" else 200
    return compute_impact_score("not_indexed", impressions)


def _load_cached_inspections(db: Session, property_id: int) -> dict[str, dict[str, Any]]:
    """Snapshot cached inspection rows into plain dicts.

    The audit loop can run long enough to cycle the DB session several times
    (see `_refresh_audit_session`) — a live ORM row would go from merely
    "expired" (auto-reloads fine on next access, as long as the session
    that created it is still open) to genuinely "detached" once that session
    is closed, raising DetachedInstanceError on the next attribute read.
    Plain dicts sidestep this entirely since nothing here needs to survive
    past the initial load.
    """
    rows = db.query(GscUrlInspection).filter(GscUrlInspection.property_id == property_id).all()
    return {
        row.url: {
            "inspected_at": row.inspected_at,
            "verdict": row.verdict,
            "coverage_state": row.coverage_state,
            "indexing_state": row.indexing_state,
            "robots_txt_state": row.robots_txt_state,
            "page_fetch_state": row.page_fetch_state,
            "google_canonical": row.google_canonical,
            "user_canonical": row.user_canonical,
            "last_crawl_time": row.last_crawl_time,
            "raw_response": row.raw_response,
        }
        for row in rows
    }


def _cache_fresh(entry: dict[str, Any], force: bool) -> bool:
    inspected_at = entry.get("inspected_at")
    if force or not inspected_at:
        return False
    if inspected_at.tzinfo is None:
        inspected_at = inspected_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - inspected_at < timedelta(days=CACHE_DAYS)


def _inspection_from_cache(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "verdict": entry["verdict"],
        "coverage_state": entry["coverage_state"],
        "indexing_state": entry["indexing_state"],
        "robots_txt_state": entry["robots_txt_state"],
        "page_fetch_state": entry["page_fetch_state"],
        "google_canonical": entry["google_canonical"],
        "user_canonical": entry["user_canonical"],
        "last_crawl_time": entry["last_crawl_time"],
        "raw_response": entry["raw_response"],
    }


def _sort_content_for_audit(
    content: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> datetime:
        url = item["url"]
        entry = cache.get(url)
        inspected = entry.get("inspected_at") if entry else None
        if not inspected:
            return datetime.min.replace(tzinfo=timezone.utc)
        if inspected.tzinfo is None:
            inspected = inspected.replace(tzinfo=timezone.utc)
        return inspected

    return sorted(content, key=sort_key)


def _upsert_inspection(
    db: Session,
    property_id: int,
    url: str,
    result: dict[str, Any],
) -> GscUrlInspection:
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
        return existing

    row = GscUrlInspection(
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
    )
    db.add(row)
    return row


REFRESH_EVERY = 10


def _refresh_audit_session(
    db: Session, audit_run_id: int, property_id: int, user_id: int
) -> tuple[Session, GscIndexAuditRun, GscProperty]:
    """Close the current session and open a fresh one, re-attaching the two
    ORM objects the audit loop keeps mutating/reading (`audit_run`, `prop`).

    The audit loop can run for a very long time (up to MAX_INSPECTIONS_PER_RUN
    URLs, each requiring a slow external API call), holding a single DB
    connection checked out of the pool almost continuously. Neon's serverless
    Postgres can silently drop a connection that's been open-but-mostly-idle
    for an extended stretch; cycling the session periodically keeps each
    checkout short-lived so `pool_pre_ping` can catch and replace a stale
    connection on the next checkout instead of failing mid-loop.
    """
    # Build the replacement session from the SAME bind/engine as the one being
    # closed, rather than importing the production SessionLocal directly --
    # run_index_audit accepts an arbitrary `db: Session` (tests pass an
    # in-memory SQLite session), so hardcoding the production session factory
    # here would silently point a mid-test refresh at the real database.
    bind = db.get_bind()
    db.close()
    new_db = sessionmaker(autocommit=False, autoflush=False, bind=bind)()
    audit_run = new_db.query(GscIndexAuditRun).filter(GscIndexAuditRun.id == audit_run_id).one()
    prop = new_db.query(GscProperty).filter(GscProperty.id == property_id).one()
    return new_db, audit_run, prop


async def run_index_audit(
    db: Session,
    user_id: int,
    property_id: int,
    audit_run_id: int,
    *,
    force: bool = False,
) -> None:
    """Run a full index audit for a GSC property linked to WordPress."""
    audit_run = (
        db.query(GscIndexAuditRun)
        .filter(
            GscIndexAuditRun.id == audit_run_id,
            GscIndexAuditRun.user_id == user_id,
            GscIndexAuditRun.property_id == property_id,
        )
        .first()
    )
    if not audit_run:
        return

    try:
        prop = (
            db.query(GscProperty)
            .filter(GscProperty.id == property_id, GscProperty.user_id == user_id)
            .first()
        )
        if not prop:
            raise ValueError("GSC property not found")
        if not prop.wordpress_site_id:
            raise ValueError("Link a WordPress site to this Search Console property before running an index audit")

        wp_site = (
            db.query(WordPressSite)
            .filter(
                WordPressSite.id == prop.wordpress_site_id,
                WordPressSite.user_id == user_id,
            )
            .first()
        )
        if not wp_site:
            raise ValueError("Linked WordPress site not found")

        audit_run.status = "running"
        db.commit()

        access_token = await get_valid_gsc_access_token(db, user_id)
        content = await list_published_content(wp_site, prop.site_url)
        cache = _load_cached_inspections(db, property_id)
        sorted_content = _sort_content_for_audit(content, cache)

        warning_message = None
        if len(sorted_content) > MAX_INSPECTIONS_PER_RUN:
            warning_message = (
                f"Only the first {MAX_INSPECTIONS_PER_RUN} URLs were inspected due to daily quota limits. "
                "Run the audit again tomorrow for remaining URLs."
            )
            sorted_content = sorted_content[:MAX_INSPECTIONS_PER_RUN]

        audit_run.total_urls = len(sorted_content)
        audit_run.processed_urls = 0
        audit_run.not_indexed_count = 0
        db.commit()

        preserved_statuses = {"approved", "dismissed", "snoozed"}
        existing_items = (
            db.query(SeoActionItem)
            .filter(
                SeoActionItem.property_id == property_id,
                SeoActionItem.action_type == "not_indexed",
            )
            .all()
        )
        preserved_keys = {
            (item.page or ""): item
            for item in existing_items
            if item.status in preserved_statuses
        }

        db.query(SeoActionItem).filter(
            SeoActionItem.property_id == property_id,
            SeoActionItem.action_type == "not_indexed",
            SeoActionItem.status == "pending_review",
        ).delete(synchronize_session=False)
        db.commit()

        published_map = _build_published_post_map(db, user_id)
        not_indexed_count = 0
        api_calls = 0

        for item in sorted_content:
            url = item["url"]
            cached = cache.get(url)
            used_api = False

            if cached and _cache_fresh(cached, force):
                result = _inspection_from_cache(cached)
            else:
                result = await inspect_url(access_token, prop.site_url, url)
                _upsert_inspection(db, property_id, url, result)
                used_api = True
                api_calls += 1
                await asyncio.sleep(GSC_CALL_DELAY_SECONDS)

            audit_run.processed_urls += 1

            if is_not_indexed(result):
                not_indexed_count += 1
                if url in preserved_keys:
                    db.commit()
                    if audit_run.processed_urls % REFRESH_EVERY == 0:
                        db, audit_run, prop = _refresh_audit_session(
                            db, audit_run_id, property_id, user_id
                        )
                    continue

                live_signals = await fetch_page_signals(url)
                wp_meta = {
                    "wp_post_id": item.get("wp_post_id"),
                    "wp_post_type": item.get("wp_post_type"),
                    "title": item.get("title"),
                    "modified_gmt": item.get("modified_gmt"),
                }
                diagnosis = await diagnose_non_indexed_url(result, live_signals, wp_meta)

                linked = published_map.get(normalize_page_url(url))
                severity = _severity_for_not_indexed(item.get("modified_gmt"))
                metric_snapshot = {
                    "verdict": result.get("verdict"),
                    "coverage_state": result.get("coverage_state"),
                    "indexing_state": result.get("indexing_state"),
                    "robots_txt_state": result.get("robots_txt_state"),
                    "page_fetch_state": result.get("page_fetch_state"),
                    "last_crawl_time": (
                        result.get("last_crawl_time").isoformat()
                        if result.get("last_crawl_time") else None
                    ),
                    "google_canonical": result.get("google_canonical"),
                    "user_canonical": result.get("user_canonical"),
                    "wp_post_id": item.get("wp_post_id"),
                    "wp_post_type": item.get("wp_post_type"),
                    "live_signals": live_signals,
                    "diagnosis": diagnosis,
                }

                db.add(
                    SeoActionItem(
                        property_id=property_id,
                        user_id=user_id,
                        action_type="not_indexed",
                        severity=severity,
                        page=url,
                        query=None,
                        metric_snapshot=metric_snapshot,
                        suggestion=diagnosis.get("explanation"),
                        impact_score=_impact_for_not_indexed(severity),
                        status="pending_review",
                        linked_published_post_id=linked[0] if linked else None,
                        linked_workflow_id=linked[1] if linked else None,
                    )
                )

            if audit_run.processed_urls % REFRESH_EVERY == 0:
                audit_run.not_indexed_count = not_indexed_count
                db.commit()
                db, audit_run, prop = _refresh_audit_session(
                    db, audit_run_id, property_id, user_id
                )

        audit_run.not_indexed_count = not_indexed_count
        audit_run.status = "completed"
        audit_run.warning_message = warning_message
        audit_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "Index audit completed for property %s: %s URLs, %s not indexed, %s API calls",
            property_id,
            audit_run.processed_urls,
            not_indexed_count,
            api_calls,
        )
    except Exception as exc:
        logger.exception("Index audit failed for property %s: %s", property_id, exc)
        audit_run.status = "failed"
        audit_run.error_message = str(exc)
        audit_run.completed_at = datetime.now(timezone.utc)
        db.commit()


async def bg_run_index_audit(
    property_id: int,
    user_id: int,
    audit_run_id: int,
    *,
    force: bool = False,
) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        await run_index_audit(db, user_id, property_id, audit_run_id, force=force)
    finally:
        db.close()
