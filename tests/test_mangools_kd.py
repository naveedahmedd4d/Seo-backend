"""Mangools KD estimation tests."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.trending_keywords import TrendingKeywords
from app.services.auth_service import create_access_token, get_password_hash
from app.services.mangools_service import (
    MangoolsQuota,
    map_keyword_import_item,
    parse_mangools_quota,
)

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
def auth_headers(db_session):
    from app.models.user import User

    user = User(
        email="kd@test.com",
        password_hash=get_password_hash("password123"),
        full_name="KD Tester",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}, user


def test_map_keyword_import_item_seo_to_kd():
    row = map_keyword_import_item({"kw": "seo agency", "seo": 42, "sv": 1200})
    assert row == {"keyword": "seo agency", "kd": 42, "mangools_sv": 1200}


def test_map_keyword_import_item_null_seo():
    row = map_keyword_import_item({"kw": "seo", "seo": None, "sv": 100})
    assert row is not None
    assert row["kd"] is None
    assert row["mangools_sv"] == 100


def test_parse_quota_prefers_related_keywords_not_checked():
    quota = parse_mangools_quota(
        {
            "upgraded_account": True,
            "reqs-per-short-period": 3,
            "resources": {
                "checked-keywords-ranks": {"limit": 1651, "remaining": 1651, "reset": 1},
                "related-keywords": {"limit": 5, "remaining": 3, "reset": 3600},
            },
        }
    )
    assert quota.remaining == 3
    assert quota.limit == 5
    assert quota.resource_key == "related-keywords"
    assert quota.short_period_max == 3
    assert quota.reset_seconds == 3600


def test_parse_quota_caps_free_inflated_pool():
    quota = parse_mangools_quota(
        {
            "upgraded_account": False,
            "resources": {
                "related-keywords": {"limit": 500, "remaining": 497, "reset": 1},
            },
        }
    )
    assert quota.remaining == 5
    assert quota.limit == 5
    assert quota.upgraded_account is False


def test_estimate_kd_requires_selection(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    monkeypatch.setattr(
        "app.services.mangools_service.settings.mangools_api_token", "test-token"
    )
    response = client.post(
        "/api/trending/keywords/estimate-kd",
        headers=headers,
        json={"source": "trending", "keyword_ids": []},
    )
    assert response.status_code == 400
    assert "Select" in response.json()["detail"]


def test_estimate_kd_missing_token(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    monkeypatch.setattr("app.services.mangools_service.settings.mangools_api_token", None)
    response = client.post(
        "/api/trending/keywords/estimate-kd",
        headers=headers,
        json={"source": "trending", "keyword_ids": [1]},
    )
    assert response.status_code == 503
    assert "MANGOOLS_API_TOKEN" in response.json()["detail"]


def test_estimate_kd_scores_selected_missing_only(
    client, auth_headers, db_session, monkeypatch
):
    headers, user = auth_headers
    monkeypatch.setattr(
        "app.services.mangools_service.settings.mangools_api_token", "test-token"
    )
    pending = TrendingKeywords(
        keyword="Need KD",
        data={},
        source="trending",
        user_id=user.id,
        fetched_at=datetime.utcnow(),
    )
    already = TrendingKeywords(
        keyword="Has KD",
        data={},
        source="trending",
        user_id=user.id,
        kd=55,
        fetched_at=datetime.utcnow(),
    )
    other = TrendingKeywords(
        keyword="Not Selected",
        data={},
        source="trending",
        user_id=user.id,
        fetched_at=datetime.utcnow(),
    )
    db_session.add_all([pending, already, other])
    db_session.commit()

    async def fake_import(keywords, location_id=0, language_id=1000):
        assert keywords == ["Need KD"]
        return [{"keyword": "Need KD", "kd": 28, "mangools_sv": 900}]

    async def fake_quota():
        return MangoolsQuota(remaining=5, limit=5, upgraded_account=False)

    monkeypatch.setattr(
        "app.api.trending.fetch_mangools_quota",
        AsyncMock(side_effect=fake_quota),
    )
    monkeypatch.setattr("app.api.trending.cooldown_before_import", AsyncMock())
    monkeypatch.setattr(
        "app.api.trending.import_keyword_metrics",
        AsyncMock(side_effect=fake_import),
    )

    response = client.post(
        "/api/trending/keywords/estimate-kd",
        headers=headers,
        json={"source": "trending", "keyword_ids": [pending.id, already.id]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scored"] == 1
    assert data["remaining_before"] == 5
    assert "Lookups left" in data["message"]
    db_session.refresh(pending)
    db_session.refresh(already)
    db_session.refresh(other)
    assert pending.kd == 28
    assert already.kd == 55
    assert other.kd is None


def test_estimate_kd_respects_keyword_ids_and_cap(
    client, auth_headers, db_session, monkeypatch
):
    headers, user = auth_headers
    monkeypatch.setattr(
        "app.services.mangools_service.settings.mangools_api_token", "test-token"
    )
    rows = [
        TrendingKeywords(keyword=f"Kw {i}", data={}, source="manual", user_id=user.id)
        for i in range(5)
    ]
    db_session.add_all(rows)
    db_session.commit()
    selected = [rows[0].id, rows[1].id, rows[2].id]

    async def fake_import(keywords, location_id=0, language_id=1000):
        assert len(keywords) == 2  # capped by remaining=2
        return [{"keyword": kw, "kd": 10, "mangools_sv": None} for kw in keywords]

    monkeypatch.setattr(
        "app.api.trending.fetch_mangools_quota",
        AsyncMock(return_value=MangoolsQuota(remaining=2, limit=5)),
    )
    monkeypatch.setattr("app.api.trending.cooldown_before_import", AsyncMock())
    monkeypatch.setattr(
        "app.api.trending.import_keyword_metrics",
        AsyncMock(side_effect=fake_import),
    )

    response = client.post(
        "/api/trending/keywords/estimate-kd",
        headers=headers,
        json={"source": "manual", "keyword_ids": selected},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scored"] == 2
    assert data["skipped"] == 1
    assert data["remaining_after"] == 0


def test_estimate_kd_null_seo_does_not_set_kd(
    client, auth_headers, db_session, monkeypatch
):
    headers, user = auth_headers
    monkeypatch.setattr(
        "app.services.mangools_service.settings.mangools_api_token", "test-token"
    )
    row = TrendingKeywords(keyword="No Seo", data={}, source="manual", user_id=user.id)
    db_session.add(row)
    db_session.commit()

    monkeypatch.setattr(
        "app.api.trending.fetch_mangools_quota",
        AsyncMock(return_value=MangoolsQuota(remaining=5, limit=5)),
    )
    monkeypatch.setattr("app.api.trending.cooldown_before_import", AsyncMock())
    monkeypatch.setattr(
        "app.api.trending.import_keyword_metrics",
        AsyncMock(return_value=[{"keyword": "No Seo", "kd": None, "mangools_sv": 10}]),
    )

    response = client.post(
        "/api/trending/keywords/estimate-kd",
        headers=headers,
        json={"source": "manual", "keyword_ids": [row.id]},
    )
    assert response.status_code == 200
    assert response.json()["scored"] == 0
    db_session.refresh(row)
    assert row.kd is None


def test_mangools_limits_endpoint(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    monkeypatch.setattr(
        "app.services.mangools_service.settings.mangools_api_token", "test-token"
    )
    monkeypatch.setattr(
        "app.api.trending.fetch_mangools_quota",
        AsyncMock(
            return_value=MangoolsQuota(
                remaining=3,
                limit=5,
                reset_seconds=120,
                short_period_max=3,
                upgraded_account=False,
            )
        ),
    )
    response = client.get(
        "/api/trending/keywords/mangools-limits",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["remaining"] == 3
    assert data["limit"] == 5
    assert "3/5" in data["message"]
