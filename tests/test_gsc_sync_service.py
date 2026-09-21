"""Tests for batched Search Console metric persistence."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.gsc_page_metric import GscPageMetric
from app.models.gsc_property import GscProperty
from app.models.gsc_query_metric import GscQueryMetric
from app.services.gsc_sync_service import _upsert_page_metrics, _upsert_query_metrics


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _metric_row(**overrides):
    row = {
        "date": "2026-09-17",
        "page": "https://example.com/a",
        "query": "example query",
        "clicks": 4,
        "impressions": 40,
        "ctr": 0.1,
        "position": 3.5,
    }
    row.update(overrides)
    return row


def test_metric_upserts_insert_and_update_in_batches():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        prop = GscProperty(user_id=1, site_url="sc-domain:example.com")
        db.add(prop)
        db.commit()
        db.refresh(prop)

        assert _upsert_page_metrics(
            db,
            prop.id,
            [_metric_row(), _metric_row(page="https://example.com/b", clicks=2)],
        ) == 2
        assert _upsert_query_metrics(db, prop.id, [_metric_row()]) == 1
        db.commit()

        assert _upsert_page_metrics(db, prop.id, [_metric_row(clicks=9, impressions=90)]) == 1
        assert _upsert_query_metrics(db, prop.id, [_metric_row(clicks=7, impressions=70)]) == 1
        db.commit()

        assert db.query(GscPageMetric).count() == 2
        assert db.query(GscQueryMetric).count() == 1
        page_metric = db.query(GscPageMetric).filter(GscPageMetric.page.endswith("/a")).one()
        query_metric = db.query(GscQueryMetric).one()
        assert (page_metric.clicks, page_metric.impressions) == (9, 90)
        assert (query_metric.clicks, query_metric.impressions) == (7, 70)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
