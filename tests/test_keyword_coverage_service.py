"""Keyword site coverage: matching + sync upsert / ownership."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.keyword_site_coverage import KeywordSiteCoverage
from app.models.trending_keywords import TrendingKeywords
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.services.auth_service import create_access_token, get_password_hash
from app.services.keyword_coverage_service import (
    MATCH_SLUG_CONTAINS,
    MATCH_SLUG_EXACT,
    MATCH_TITLE_CONTAINS,
    MATCH_TITLE_EXACT,
    STATUS_EXISTING,
    STATUS_NEW,
    match_keyword,
    normalize_for_match,
    slug_from_url,
    sync_coverage_for_site,
)
from app.services.wordpress_service import encrypt_api_key

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
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def user(db_session):
    u = User(
        email="coverage@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Coverage Tester",
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def auth_headers(user):
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def wp_site(db_session, user):
    site = WordPressSite(
        user_id=user.id,
        name="Test Site",
        url="https://example.com",
        api_key=encrypt_api_key("secret"),
        username="editor",
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)
    return site


def test_normalize_for_match():
    assert normalize_for_match("  Elon Musk! ") == "elon musk"
    assert normalize_for_match("elon-musk") == "elon musk"
    assert normalize_for_match("Elon_Musk") == "elon musk"


def test_slug_from_url():
    assert slug_from_url("https://example.com/elon-musk/") == "elon-musk"
    assert slug_from_url("https://example.com/posts/elon%20musk") == "elon musk"
    assert slug_from_url("https://example.com/") == ""


def test_match_title_exact():
    items = [{"title": "Elon Musk", "url": "https://example.com/about"}]
    hit = match_keyword("elon musk", items)
    assert hit is not None
    item, method = hit
    assert item["title"] == "Elon Musk"
    assert method == MATCH_TITLE_EXACT


def test_match_slug_exact():
    items = [{"title": "About the founder", "url": "https://example.com/elon-musk"}]
    hit = match_keyword("Elon Musk", items)
    assert hit is not None
    assert hit[1] == MATCH_SLUG_EXACT


def test_match_title_contains():
    items = [{"title": "Elon Musk Net Worth 2026", "url": "https://example.com/x"}]
    hit = match_keyword("Elon Musk", items)
    assert hit is not None
    assert hit[1] == MATCH_TITLE_CONTAINS


def test_match_slug_contains():
    items = [{"title": "Profile", "url": "https://example.com/elon-musk-biography"}]
    hit = match_keyword("Elon Musk", items)
    assert hit is not None
    assert hit[1] == MATCH_SLUG_CONTAINS


def test_match_no_match():
    items = [{"title": "Taylor Swift", "url": "https://example.com/taylor-swift"}]
    assert match_keyword("Elon Musk", items) is None


@pytest.mark.asyncio
async def test_sync_coverage_upsert(db_session, user, wp_site, monkeypatch):
    kw_existing = TrendingKeywords(keyword="Elon Musk", source="manual", data={}, user_id=user.id)
    kw_new = TrendingKeywords(keyword="Unknown Person", source="manual", data={}, user_id=user.id)
    db_session.add_all([kw_existing, kw_new])
    db_session.commit()
    db_session.refresh(kw_existing)
    db_session.refresh(kw_new)

    async def fake_list(_site, _url):
        return [
            {
                "title": "Elon Musk",
                "url": "https://example.com/elon-musk/",
                "modified_gmt": "2026-01-01T00:00:00",
            }
        ]

    monkeypatch.setattr(
        "app.services.keyword_coverage_service.list_published_content",
        fake_list,
    )

    result = await sync_coverage_for_site(db_session, user, wp_site.id, "manual")
    assert result["existing"] == 1
    assert result["new"] == 1
    assert result["total"] == 2

    rows = db_session.query(KeywordSiteCoverage).all()
    assert len(rows) == 2
    by_kw = {r.trending_keyword_id: r for r in rows}
    assert by_kw[kw_existing.id].status == STATUS_EXISTING
    assert by_kw[kw_existing.id].matched_url == "https://example.com/elon-musk/"
    assert by_kw[kw_existing.id].match_method == MATCH_TITLE_EXACT
    assert by_kw[kw_new.id].status == STATUS_NEW
    assert by_kw[kw_new.id].matched_url is None

    # Second sync updates compared_at / upserts
    first_compared = by_kw[kw_existing.id].compared_at
    result2 = await sync_coverage_for_site(db_session, user, wp_site.id, "manual")
    assert result2["total"] == 2
    db_session.expire_all()
    rows2 = db_session.query(KeywordSiteCoverage).all()
    assert len(rows2) == 2
    assert all(r.compared_at is not None for r in rows2)


@pytest.mark.asyncio
async def test_sync_rejects_other_users_site(db_session, user, monkeypatch):
    other = User(
        email="other@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Other",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    site = WordPressSite(
        user_id=other.id,
        name="Other Site",
        url="https://other.com",
        api_key=encrypt_api_key("secret"),
        username="editor",
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)

    monkeypatch.setattr(
        "app.services.keyword_coverage_service.list_published_content",
        AsyncMock(return_value=[]),
    )

    with pytest.raises(HTTPException) as exc:
        await sync_coverage_for_site(db_session, user, site.id, "manual")
    assert exc.value.status_code == 404


def test_sync_endpoint_and_list_coverage(client, auth_headers, db_session, user, wp_site, monkeypatch):
    kw = TrendingKeywords(keyword="Elon Musk", source="manual", data={}, user_id=user.id)
    db_session.add(kw)
    db_session.commit()
    db_session.refresh(kw)

    async def fake_list(_site, _url):
        return [{"title": "Elon Musk Biography", "url": "https://example.com/elon-musk"}]

    monkeypatch.setattr(
        "app.services.keyword_coverage_service.list_published_content",
        fake_list,
    )

    sync_res = client.post(
        "/api/trending/keywords/coverage/sync",
        headers=auth_headers,
        json={"wordpress_site_id": wp_site.id, "source": "manual"},
    )
    assert sync_res.status_code == 200
    body = sync_res.json()
    assert body["existing"] == 1
    assert body["new"] == 0

    list_res = client.get(
        f"/api/trending/keywords?source=manual&wordpress_site_id={wp_site.id}",
        headers=auth_headers,
    )
    assert list_res.status_code == 200
    keywords = list_res.json()["keywords"]
    assert len(keywords) == 1
    assert keywords[0]["coverage"]["status"] == "existing"
    assert keywords[0]["coverage"]["matched_title"] == "Elon Musk Biography"

    filtered = client.get(
        f"/api/trending/keywords?source=manual&wordpress_site_id={wp_site.id}&coverage_status=new",
        headers=auth_headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["count"] == 0

    existing_only = client.get(
        f"/api/trending/keywords?source=manual&wordpress_site_id={wp_site.id}&coverage_status=existing",
        headers=auth_headers,
    )
    assert existing_only.status_code == 200
    assert existing_only.json()["count"] == 1
