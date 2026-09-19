"""Unit tests for GSC index audit orchestrator."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.gsc_index_audit_run import GscIndexAuditRun
from app.models.gsc_property import GscProperty
from app.models.gsc_url_inspection import GscUrlInspection
from app.models.seo_action_item import SeoActionItem
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.services.wordpress_service import encrypt_api_key
from app.services.gsc_index_audit_service import run_index_audit

SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def seeded_property(db_session):
    user = User(email="audit@test.com", password_hash="hash")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    wp = WordPressSite(
        user_id=user.id,
        name="Test WP",
        url="https://example.com",
        username="admin",
        api_key=encrypt_api_key("app-password"),
    )
    db_session.add(wp)
    db_session.commit()
    db_session.refresh(wp)

    prop = GscProperty(
        user_id=user.id,
        site_url="sc-domain:example.com",
        display_name="example.com",
        wordpress_site_id=wp.id,
    )
    db_session.add(prop)
    db_session.commit()
    db_session.refresh(prop)

    audit_run = GscIndexAuditRun(
        property_id=prop.id,
        user_id=user.id,
        status="pending",
    )
    db_session.add(audit_run)
    db_session.commit()
    db_session.refresh(audit_run)

    return user, prop, wp, audit_run


@pytest.mark.asyncio
async def test_run_index_audit_creates_not_indexed_action(db_session, seeded_property):
    user, prop, _wp, audit_run = seeded_property

    wp_content = [{
        "url": "https://example.com/missing-post/",
        "wp_post_id": 42,
        "wp_post_type": "post",
        "title": "Missing Post",
        "modified_gmt": "2026-06-01T00:00:00",
    }]
    inspection = {
        "verdict": "FAIL",
        "coverage_state": "Crawled - currently not indexed",
        "indexing_state": "INDEXING_ALLOWED",
        "robots_txt_state": "ALLOWED",
        "page_fetch_state": "SUCCESSFUL",
        "google_canonical": None,
        "user_canonical": "https://example.com/missing-post/",
        "last_crawl_time": datetime.now(timezone.utc),
        "raw_response": {},
    }
    diagnosis = {
        "root_cause": "thin_content",
        "confidence": 0.8,
        "explanation": "Page is thin.",
        "recommended_fixes": [{"type": "expand_content", "description": "Add content", "auto_applicable": False}],
    }

    with patch(
        "app.services.gsc_index_audit_service.get_valid_gsc_access_token",
        new_callable=AsyncMock,
        return_value="token",
    ), patch(
        "app.services.gsc_index_audit_service.list_published_content",
        new_callable=AsyncMock,
        return_value=wp_content,
    ), patch(
        "app.services.gsc_index_audit_service.inspect_url",
        new_callable=AsyncMock,
        return_value=inspection,
    ), patch(
        "app.services.gsc_index_audit_service.fetch_page_signals",
        new_callable=AsyncMock,
        return_value={"status_code": 200, "word_count": 120, "has_noindex": False},
    ), patch(
        "app.services.gsc_index_audit_service.diagnose_non_indexed_url",
        new_callable=AsyncMock,
        return_value=diagnosis,
    ):
        await run_index_audit(db_session, user.id, prop.id, audit_run.id)

    db_session.refresh(audit_run)
    assert audit_run.status == "completed"
    assert audit_run.not_indexed_count == 1

    actions = (
        db_session.query(SeoActionItem)
        .filter(SeoActionItem.property_id == prop.id, SeoActionItem.action_type == "not_indexed")
        .all()
    )
    assert len(actions) == 1
    assert actions[0].page == "https://example.com/missing-post/"


@pytest.mark.asyncio
async def test_run_index_audit_skips_fresh_cache(db_session, seeded_property):
    user, prop, _wp, audit_run = seeded_property

    cached = GscUrlInspection(
        property_id=prop.id,
        url="https://example.com/cached/",
        verdict="PASS",
        coverage_state="Submitted and indexed",
        indexing_state="INDEXING_ALLOWED",
        inspected_at=datetime.now(timezone.utc),
    )
    db_session.add(cached)
    db_session.commit()

    wp_content = [{
        "url": "https://example.com/cached/",
        "wp_post_id": 1,
        "wp_post_type": "post",
        "title": "Cached",
        "modified_gmt": "2026-06-01T00:00:00",
    }]

    inspect_mock = AsyncMock()

    with patch(
        "app.services.gsc_index_audit_service.get_valid_gsc_access_token",
        new_callable=AsyncMock,
        return_value="token",
    ), patch(
        "app.services.gsc_index_audit_service.list_published_content",
        new_callable=AsyncMock,
        return_value=wp_content,
    ), patch(
        "app.services.gsc_index_audit_service.inspect_url",
        inspect_mock,
    ):
        await run_index_audit(db_session, user.id, prop.id, audit_run.id)

    inspect_mock.assert_not_called()
    db_session.refresh(audit_run)
    assert audit_run.status == "completed"
    assert audit_run.not_indexed_count == 0


@pytest.mark.asyncio
async def test_run_index_audit_re_inspects_stale_cache(db_session, seeded_property):
    user, prop, _wp, audit_run = seeded_property

    cached = GscUrlInspection(
        property_id=prop.id,
        url="https://example.com/stale/",
        verdict="PASS",
        coverage_state="Submitted and indexed",
        indexing_state="INDEXING_ALLOWED",
        inspected_at=datetime.now(timezone.utc) - timedelta(days=10),
    )
    db_session.add(cached)
    db_session.commit()

    wp_content = [{
        "url": "https://example.com/stale/",
        "wp_post_id": 2,
        "wp_post_type": "post",
        "title": "Stale",
        "modified_gmt": "2026-06-01T00:00:00",
    }]
    inspection = {
        "verdict": "PASS",
        "coverage_state": "Submitted and indexed",
        "indexing_state": "INDEXING_ALLOWED",
        "robots_txt_state": "ALLOWED",
        "page_fetch_state": "SUCCESSFUL",
        "google_canonical": "https://example.com/stale/",
        "user_canonical": "https://example.com/stale/",
        "last_crawl_time": datetime.now(timezone.utc),
        "raw_response": {},
    }

    inspect_mock = AsyncMock(return_value=inspection)

    with patch(
        "app.services.gsc_index_audit_service.get_valid_gsc_access_token",
        new_callable=AsyncMock,
        return_value="token",
    ), patch(
        "app.services.gsc_index_audit_service.list_published_content",
        new_callable=AsyncMock,
        return_value=wp_content,
    ), patch(
        "app.services.gsc_index_audit_service.inspect_url",
        inspect_mock,
    ):
        await run_index_audit(db_session, user.id, prop.id, audit_run.id)

    inspect_mock.assert_called_once()


@pytest.mark.asyncio
async def test_run_index_audit_survives_session_refresh(db_session, seeded_property):
    """With enough URLs to cross REFRESH_EVERY, the loop cycles its DB session
    mid-run (see `_refresh_audit_session`) -- this must not raise
    DetachedInstanceError on the cached-inspection lookups or lose track of
    `audit_run`/`prop` across the cycle.
    """
    user, prop, _wp, audit_run = seeded_property
    # Capture plain ints up front: production callers (bg_run_index_audit) only
    # ever pass plain IDs, never hold an ORM reference across the call the way
    # this fixture's `audit_run`/`prop` objects would -- and once the loop
    # cycles its internal session, those fixture-bound instances go from
    # expired to fully detached, so touching them again below isn't valid.
    audit_run_id = audit_run.id
    prop_id = prop.id
    user_id = user.id

    url_count = 25  # > REFRESH_EVERY (10), so at least two refreshes happen
    wp_content = [
        {
            "url": f"https://example.com/post-{i}/",
            "wp_post_id": i,
            "wp_post_type": "post",
            "title": f"Post {i}",
            "modified_gmt": "2026-06-01T00:00:00",
        }
        for i in range(url_count)
    ]
    inspection = {
        "verdict": "FAIL",
        "coverage_state": "Crawled - currently not indexed",
        "indexing_state": "INDEXING_ALLOWED",
        "robots_txt_state": "ALLOWED",
        "page_fetch_state": "SUCCESSFUL",
        "google_canonical": None,
        "user_canonical": None,
        "last_crawl_time": datetime.now(timezone.utc),
        "raw_response": {},
    }
    diagnosis = {
        "root_cause": "thin_content",
        "confidence": 0.8,
        "explanation": "Page is thin.",
        "recommended_fixes": [],
    }

    with patch(
        "app.services.gsc_index_audit_service.get_valid_gsc_access_token",
        new_callable=AsyncMock,
        return_value="token",
    ), patch(
        "app.services.gsc_index_audit_service.list_published_content",
        new_callable=AsyncMock,
        return_value=wp_content,
    ), patch(
        "app.services.gsc_index_audit_service.inspect_url",
        new_callable=AsyncMock,
        return_value=inspection,
    ), patch(
        "app.services.gsc_index_audit_service.fetch_page_signals",
        new_callable=AsyncMock,
        return_value={"status_code": 200, "word_count": 50, "has_noindex": False},
    ), patch(
        "app.services.gsc_index_audit_service.diagnose_non_indexed_url",
        new_callable=AsyncMock,
        return_value=diagnosis,
    ):
        await run_index_audit(db_session, user_id, prop_id, audit_run_id)

    # The loop cycles its own session internally (see _refresh_audit_session),
    # which closes the session this test started with -- re-query fresh
    # rather than db_session.refresh(audit_run), since a closed session no
    # longer tracks that instance as persistent.
    verify_db = TestingSessionLocal()
    try:
        final_run = (
            verify_db.query(GscIndexAuditRun).filter(GscIndexAuditRun.id == audit_run_id).one()
        )
        assert final_run.status == "completed"
        assert final_run.processed_urls == url_count
        assert final_run.not_indexed_count == url_count

        actions = (
            verify_db.query(SeoActionItem)
            .filter(SeoActionItem.property_id == prop_id, SeoActionItem.action_type == "not_indexed")
            .all()
        )
        assert len(actions) == url_count
    finally:
        verify_db.close()
