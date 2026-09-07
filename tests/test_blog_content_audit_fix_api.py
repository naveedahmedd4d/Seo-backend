"""API tests for blog content audit fixes."""

from unittest.mock import AsyncMock, patch

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


def _audit_setup(db_session, user: User):
    site = WordPressSite(
        user_id=user.id,
        name="Blog",
        url="https://example.com",
        username="admin",
        api_key=encrypt_api_key("app-password"),
    )
    db_session.add(site)
    db_session.commit()
    run = BlogContentAuditRun(user_id=user.id, wordpress_site_id=site.id, status="completed")
    db_session.add(run)
    db_session.commit()
    audit = BlogContentAudit(
        run_id=run.id,
        url="https://example.com/post",
        title="Post",
        article_type="net_worth",
        word_count=900,
        word_target=2500,
        wp_post_id=42,
        findings=[{"category": "seo", "message": "Missing meta", "severity": "high"}],
    )
    db_session.add(audit)
    db_session.commit()
    return run, audit


def test_generate_fixes(client, db_session):
    user = _user(db_session, "fix-api@test.com")
    run, audit = _audit_setup(db_session, user)
    wp_mock = {
        "success": True,
        "title": "Post",
        "content_html": "<p>Short body</p>",
        "meta_description": None,
    }
    with patch(
        "app.services.blog_content_audit_fix_service.fetch_wp_content_for_audit",
        new=AsyncMock(return_value=wp_mock),
    ), patch(
        "app.services.blog_content_audit_fix_generators.generate_content",
        new=AsyncMock(return_value="SEO meta description for the post about net worth."),
    ), patch(
        "app.services.blog_content_audit_fix_generators.expand_thin_blog_body",
        new=AsyncMock(return_value="<p>Expanded body content</p>"),
    ):
        resp = client.post(
            f"/api/website-audit/content-audit/runs/{run.id}/posts/{audit.id}/fixes/generate",
            json={"force": True},
            headers=_headers(user),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1


def test_list_fixes_empty(client, db_session):
    user = _user(db_session, "fix-list@test.com")
    run, audit = _audit_setup(db_session, user)
    resp = client.get(
        f"/api/website-audit/content-audit/runs/{run.id}/posts/{audit.id}/fixes",
        headers=_headers(user),
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_cross_user_denied(client, db_session):
    owner = _user(db_session, "fix-owner@test.com")
    other = _user(db_session, "fix-other@test.com")
    run, audit = _audit_setup(db_session, owner)
    resp = client.get(
        f"/api/website-audit/content-audit/runs/{run.id}/posts/{audit.id}/fixes",
        headers=_headers(other),
    )
    assert resp.status_code == 404
