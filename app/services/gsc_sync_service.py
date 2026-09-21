import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import insert as sqlalchemy_insert
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
BULK_UPSERT_SIZE = 500


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _upsert_in_batches(
    db: Session,
    model,
    records: list[dict],
    conflict_columns: list[str],
) -> None:
    """Upsert metric rows without one database round trip per Google row."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        insert = sqlalchemy_insert

    for offset in range(0, len(records), BULK_UPSERT_SIZE):
        batch = records[offset : offset + BULK_UPSERT_SIZE]
        statement = insert(model).values(batch)
        if not hasattr(statement, "on_conflict_do_update"):
            # Search Console is deployed on Postgres; this fallback keeps the
            # helper usable with less common SQLAlchemy test/dev dialects.
            db.bulk_insert_mappings(model, batch)
            continue
        statement = statement.on_conflict_do_update(
            index_elements=conflict_columns,
            set_={
                "clicks": statement.excluded.clicks,
                "impressions": statement.excluded.impressions,
                "ctr": statement.excluded.ctr,
                "position": statement.excluded.position,
            },
        )
        db.execute(statement)


def _upsert_page_metrics(db: Session, property_id: int, rows: list[dict]) -> int:
    if not rows:
        return 0
    records_by_key: dict[tuple[date, str], dict] = {}
    for row in rows:
        page = row.get("page")
        dt = row.get("date")
        if not page or not dt:
            continue
        metric_date = _parse_date(dt) if isinstance(dt, str) else dt
        records_by_key[(metric_date, page)] = {
            "property_id": property_id,
            "date": metric_date,
            "page": page,
            "clicks": row["clicks"],
            "impressions": row["impressions"],
            "ctr": row["ctr"],
            "position": row["position"],
        }

    records = list(records_by_key.values())
    _upsert_in_batches(
        db,
        GscPageMetric,
        records,
        ["property_id", "date", "page"],
    )
    return len(records)


def _upsert_query_metrics(db: Session, property_id: int, rows: list[dict]) -> int:
    if not rows:
        return 0
    records_by_key: dict[tuple[date, str, str], dict] = {}
    for row in rows[:MAX_QUERY_ROWS]:
        query = row.get("query")
        page = row.get("page")
        dt = row.get("date")
        if not query or not page or not dt:
            continue
        metric_date = _parse_date(dt) if isinstance(dt, str) else dt
        records_by_key[(metric_date, query, page)] = {
            "property_id": property_id,
            "date": metric_date,
            "query": query,
            "page": page,
            "clicks": row["clicks"],
            "impressions": row["impressions"],
            "ctr": row["ctr"],
            "position": row["position"],
        }

    records = list(records_by_key.values())
    _upsert_in_batches(
        db,
        GscQueryMetric,
        records,
        ["property_id", "date", "query", "page"],
    )
    return len(records)


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

    logger.info(
        "GSC fetched property=%s current_pages=%s prior_pages=%s "
        "current_queries=%s prior_queries=%s",
        property_id,
        len(page_rows),
        len(prior_page_rows),
        len(query_rows),
        len(prior_query_rows),
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
