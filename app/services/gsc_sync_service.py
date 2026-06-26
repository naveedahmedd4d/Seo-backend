import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.gsc_page_metric import GscPageMetric
from app.models.gsc_property import GscProperty
from app.models.gsc_query_metric import GscQueryMetric
from app.services.gsc_analysis_service import run_analysis_for_property
from app.services.gsc_oauth_service import get_valid_gsc_access_token
from app.services.gsc_service import query_search_analytics

logger = logging.getLogger(__name__)

SYNC_DAYS = 28
PRIOR_DAYS = 28
MAX_QUERY_ROWS = 50000


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _upsert_page_metrics(db: Session, property_id: int, rows: list[dict]) -> int:
    if not rows:
        return 0
    count = 0
    for row in rows:
        page = row.get("page")
        dt = row.get("date")
        if not page or not dt:
            continue
        metric_date = _parse_date(dt) if isinstance(dt, str) else dt
        existing = (
            db.query(GscPageMetric)
            .filter(
                GscPageMetric.property_id == property_id,
                GscPageMetric.date == metric_date,
                GscPageMetric.page == page,
            )
            .first()
        )
        if existing:
            existing.clicks = row["clicks"]
            existing.impressions = row["impressions"]
            existing.ctr = row["ctr"]
            existing.position = row["position"]
        else:
            db.add(
                GscPageMetric(
                    property_id=property_id,
                    date=metric_date,
                    page=page,
                    clicks=row["clicks"],
                    impressions=row["impressions"],
                    ctr=row["ctr"],
                    position=row["position"],
                )
            )
        count += 1
    return count


def _upsert_query_metrics(db: Session, property_id: int, rows: list[dict]) -> int:
    if not rows:
        return 0
    count = 0
    for row in rows[:MAX_QUERY_ROWS]:
        query = row.get("query")
        page = row.get("page")
        dt = row.get("date")
        if not query or not page or not dt:
            continue
        metric_date = _parse_date(dt) if isinstance(dt, str) else dt
        existing = (
            db.query(GscQueryMetric)
            .filter(
                GscQueryMetric.property_id == property_id,
                GscQueryMetric.date == metric_date,
                GscQueryMetric.query == query,
                GscQueryMetric.page == page,
            )
            .first()
        )
        if existing:
            existing.clicks = row["clicks"]
            existing.impressions = row["impressions"]
            existing.ctr = row["ctr"]
            existing.position = row["position"]
        else:
            db.add(
                GscQueryMetric(
                    property_id=property_id,
                    date=metric_date,
                    query=query,
                    page=page,
                    clicks=row["clicks"],
                    impressions=row["impressions"],
                    ctr=row["ctr"],
                    position=row["position"],
                )
            )
        count += 1
    return count


async def sync_property(db: Session, user_id: int, property_id: int) -> dict:
    prop = (
        db.query(GscProperty)
        .filter(GscProperty.id == property_id, GscProperty.user_id == user_id)
        .first()
    )
    if not prop:
        raise ValueError("GSC property not found")

    access_token = await get_valid_gsc_access_token(db, user_id)

    # GSC data is typically 2-3 days behind
    end_date = date.today() - timedelta(days=3)
    current_start = end_date - timedelta(days=SYNC_DAYS - 1)
    prior_end = current_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=PRIOR_DAYS - 1)

    page_rows = await query_search_analytics(
        access_token,
        prop.site_url,
        current_start,
        end_date,
        ["date", "page"],
    )
    prior_page_rows = await query_search_analytics(
        access_token,
        prop.site_url,
        prior_start,
        prior_end,
        ["date", "page"],
    )
    query_rows = await query_search_analytics(
        access_token,
        prop.site_url,
        current_start,
        end_date,
        ["date", "query", "page"],
    )
    prior_query_rows = await query_search_analytics(
        access_token,
        prop.site_url,
        prior_start,
        prior_end,
        ["date", "query", "page"],
    )

    page_count = _upsert_page_metrics(db, property_id, page_rows)
    _upsert_page_metrics(db, property_id, prior_page_rows)
    query_count = _upsert_query_metrics(db, property_id, query_rows)
    _upsert_query_metrics(db, property_id, prior_query_rows)

    prop.last_synced_at = datetime.now(timezone.utc)
    db.commit()

    action_count = run_analysis_for_property(db, user_id, property_id)
    db.commit()

    return {
        "property_id": property_id,
        "page_rows_synced": page_count,
        "query_rows_synced": query_count,
        "actions_generated": action_count,
        "data_through": end_date.isoformat(),
        "last_synced_at": prop.last_synced_at.isoformat() if prop.last_synced_at else None,
    }


async def sync_all_properties(db: Session, user_id: int) -> list[dict]:
    props = db.query(GscProperty).filter(GscProperty.user_id == user_id).all()
    results = []
    for prop in props:
        try:
            results.append(await sync_property(db, user_id, prop.id))
        except Exception as exc:
            logger.exception("Failed to sync property %s: %s", prop.id, exc)
            results.append({"property_id": prop.id, "error": str(exc)})
    return results
