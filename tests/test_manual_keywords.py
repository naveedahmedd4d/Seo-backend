"""Manual keywords API tests."""

import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.trending_keywords import TrendingKeywords
from app.services.auth_service import create_access_token, get_password_hash

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
        email="manual@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Manual Tester",
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def test_add_manual_keywords_creates_rows(client, auth_headers, db_session):
    response = client.post(
        "/api/trending/keywords/manual",
        headers=auth_headers,
        json={
            "keywords": ["Custom Topic A", "Custom Topic B"],
            "category": "Technology & AI",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    rows = db_session.query(TrendingKeywords).all()
    assert len(rows) == 2
    assert all(r.source == "manual" for r in rows)
    assert {r.keyword for r in rows} == {"Custom Topic A", "Custom Topic B"}


def test_duplicate_manual_keyword_skipped(client, auth_headers):
    payload = {"keywords": ["Duplicate Me"], "category": "Sports"}
    first = client.post("/api/trending/keywords/manual", headers=auth_headers, json=payload)
    assert first.status_code == 200
    second = client.post("/api/trending/keywords/manual", headers=auth_headers, json=payload)
    assert second.status_code == 409


def test_get_manual_excludes_trending(client, auth_headers, db_session):
    db_session.add(
        TrendingKeywords(
            keyword="Trending Only",
            data={"value": 100},
            source="trending",
            fetched_at=datetime.utcnow(),
        )
    )
    db_session.add(
        TrendingKeywords(
            keyword="Manual Only",
            data={},
            source="manual",
            fetched_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    manual = client.get("/api/trending/keywords?source=manual", headers=auth_headers)
    assert manual.status_code == 200
    manual_keywords = [k["keyword"] for k in manual.json()["keywords"]]
    assert manual_keywords == ["Manual Only"]

    trending = client.get("/api/trending/keywords?source=trending", headers=auth_headers)
    assert trending.status_code == 200
    trending_keywords = [k["keyword"] for k in trending.json()["keywords"]]
    assert trending_keywords == ["Trending Only"]


def test_trending_dedupe_ignores_manual_keyword(db_session):
    from app.services.serpapi_service import get_trending_keywords_from_db

    db_session.add(
        TrendingKeywords(
            keyword="Shared Name",
            data={},
            source="manual",
            fetched_at=datetime.utcnow(),
        )
    )
    db_session.add(
        TrendingKeywords(
            keyword="Shared Name",
            data={"value": 50},
            source="trending",
            fetched_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    rows = get_trending_keywords_from_db(db_session, hours=4)
    assert len(rows) == 1
    assert rows[0]["source"] == "trending"


def test_categorize_only_touches_trending(client, auth_headers, db_session, monkeypatch):
    db_session.add(
        TrendingKeywords(
            keyword="Manual Uncat",
            data={},
            source="manual",
            category=None,
        )
    )
    db_session.add(
        TrendingKeywords(
            keyword="Trending Uncat",
            data={},
            source="trending",
            category=None,
        )
    )
    db_session.commit()

    async def fake_categorize(keyword: str):
        return "Sports"

    monkeypatch.setattr("app.api.trending.categorize_keyword", fake_categorize)

    response = client.post(
        "/api/trending/keywords/categorize?recategorize=false",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["categorized"] == 1

    manual = db_session.query(TrendingKeywords).filter_by(keyword="Manual Uncat").one()
    trending = db_session.query(TrendingKeywords).filter_by(keyword="Trending Uncat").one()
    assert manual.category is None
    assert trending.category == "Sports"


def test_manual_completed_only_filter(client, auth_headers, db_session):
    pending = TrendingKeywords(keyword="Pending Manual", data={}, source="manual")
    bio_only = TrendingKeywords(
        keyword="Bio Only",
        data={},
        source="manual",
        bio_blog_workflow_id=42,
    )
    net_worth_only = TrendingKeywords(
        keyword="Net Worth Only",
        data={},
        source="manual",
        net_worth_blog_workflow_id=99,
    )
    db_session.add_all([pending, bio_only, net_worth_only])
    db_session.commit()

    pending_resp = client.get(
        "/api/trending/keywords?source=manual&completed_only=false",
        headers=auth_headers,
    )
    assert pending_resp.status_code == 200
    assert [k["keyword"] for k in pending_resp.json()["keywords"]] == ["Pending Manual"]

    completed_resp = client.get(
        "/api/trending/keywords?source=manual&completed_only=true",
        headers=auth_headers,
    )
    assert completed_resp.status_code == 200
    assert {k["keyword"] for k in completed_resp.json()["keywords"]} == {
        "Bio Only",
        "Net Worth Only",
    }


def test_patch_blog_workflow_biography(client, auth_headers, db_session):
    row = TrendingKeywords(keyword="Celebrity A", data={}, source="manual")
    db_session.add(row)
    db_session.commit()

    response = client.patch(
        f"/api/trending/keywords/{row.id}/blog-workflow",
        headers=auth_headers,
        json={"workflow_id": 101, "content_type": "biography"},
    )
    assert response.status_code == 200
    db_session.refresh(row)
    assert row.bio_blog_workflow_id == 101
    assert row.net_worth_blog_workflow_id is None


def test_patch_blog_workflow_net_worth(client, auth_headers, db_session):
    row = TrendingKeywords(keyword="Celebrity B", data={}, source="manual")
    db_session.add(row)
    db_session.commit()

    response = client.patch(
        f"/api/trending/keywords/{row.id}/blog-workflow",
        headers=auth_headers,
        json={"workflow_id": 202, "content_type": "net_worth"},
    )
    assert response.status_code == 200
    db_session.refresh(row)
    assert row.net_worth_blog_workflow_id == 202
    assert row.bio_blog_workflow_id is None
