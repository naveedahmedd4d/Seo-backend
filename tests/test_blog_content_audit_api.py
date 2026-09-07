"""API tests for Blog Content Review."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.blog_content_audit import BlogContentAudit
from app.models.blog_content_audit_run import BlogContentAuditRun
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.services.auth_service import create_access_token, get_password_hash
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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _user(db_session, email: str):
    user = User(email=email, password_hash=get_password_hash("secret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _headers(user: User):
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def _site(db_session, user: User):
    site = WordPressSite(
        user_id=user.id,
        name="Blog",
        url="https://example.com",
        username="admin",
        api_key=encrypt_api_key("app-password"),
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)
    return site


def test_start_content_audit(client, db_session):
    user = _user(db_session, "content-api@test.com")
    site = _site(db_session, user)
    resp = client.post(
        "/api/website-audit/content-audit/runs",
        json={"wordpress_site_id": site.id, "post_limit": 10},
        headers=_headers(user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["wordpress_site_id"] == site.id
    assert data["status"] == "pending"


def test_list_content_audit_posts(client, db_session):
    user = _user(db_session, "content-list@test.com")
    site = _site(db_session, user)
    run = BlogContentAuditRun(
        user_id=user.id,
        wordpress_site_id=site.id,
        status="completed",
        site_summary={"overall_avg": 72.5, "adsense_risk_level": "medium"},
    )
    db_session.add(run)
    db_session.commit()
    audit = BlogContentAudit(
        run_id=run.id,
        url="https://example.com/a",
        title="Post A",
        slug="post-a",
        article_type="net_worth",
        word_count=900,
        word_target=2500,
        word_target_met=False,
        overall_score=55.0,
        seo_score=60.0,
        content_quality_score=50.0,
        search_intent_score=55.0,
        content_depth_score=40.0,
        originality_score=70.0,
        eeat_score=45.0,
        adsense_quality_score=50.0,
        findings=[{"severity": "high", "category": "content_depth", "message": "Below word target"}],
    )
    db_session.add(audit)
    db_session.commit()

    resp = client.get(
        f"/api/website-audit/content-audit/runs/{run.id}/posts",
        headers=_headers(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["overall_score"] == 55.0
    assert len(body["items"][0]["top_findings"]) == 1


def test_get_content_audit_detail(client, db_session):
    user = _user(db_session, "content-detail@test.com")
    site = _site(db_session, user)
    run = BlogContentAuditRun(user_id=user.id, wordpress_site_id=site.id, status="completed")
    db_session.add(run)
    db_session.commit()
    audit = BlogContentAudit(
        run_id=run.id,
        url="https://example.com/b",
        title="Post B",
        article_type="biography",
        word_count=2000,
        word_target=2500,
        word_target_met=False,
        overall_score=68.0,
        gap_matrix=[{"topic": "Early Life", "status": "missing", "evidence": "test"}],
        adsense_risks=[{"code": "shallow_word_count", "severity": "high", "message": "Short"}],
    )
    db_session.add(audit)
    db_session.commit()

    resp = client.get(
        f"/api/website-audit/content-audit/runs/{run.id}/posts/{audit.id}",
        headers=_headers(user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["gap_matrix"][0]["topic"] == "Early Life"
    assert data["adsense_risks"][0]["code"] == "shallow_word_count"


def test_latest_content_audit(client, db_session):
    user = _user(db_session, "content-latest@test.com")
    site = _site(db_session, user)
    run = BlogContentAuditRun(
        user_id=user.id,
        wordpress_site_id=site.id,
        status="completed",
        site_summary={"overall_avg": 80.0},
    )
    db_session.add(run)
    db_session.commit()

    resp = client.get(
        f"/api/website-audit/content-audit/sites/{site.id}/latest",
        headers=_headers(user),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == run.id
