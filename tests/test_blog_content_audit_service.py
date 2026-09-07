"""Orchestrator tests with mocked external services."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.blog_content_audit import BlogContentAudit
from app.models.blog_content_audit_run import BlogContentAuditRun
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.services.auth_service import get_password_hash
from app.services.blog_content_audit_service import run_blog_content_audit
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
def audit_setup(db_session):
    user = User(email="content@test.com", password_hash=get_password_hash("secret123"))
    db_session.add(user)
    db_session.commit()
    site = WordPressSite(
        user_id=user.id,
        name="Blog",
        url="https://example.com",
        username="admin",
        api_key=encrypt_api_key("app-password"),
    )
    db_session.add(site)
    db_session.commit()
    run = BlogContentAuditRun(
        user_id=user.id,
        wordpress_site_id=site.id,
        status="pending",
        scope_json={"post_limit": 2, "skip_llm": True},
    )
    db_session.add(run)
    db_session.commit()
    return user, site, run


@pytest.mark.asyncio
async def test_run_blog_content_audit_completes(db_session, audit_setup):
    user, site, run = audit_setup
    items = [
        {"url": "https://example.com/post-a", "wp_post_id": 1, "post_type": "post", "title": "Post A"},
        {"url": "https://example.com/post-b", "wp_post_id": 2, "post_type": "post", "title": "Post B"},
    ]
    wp_response = {
        "success": True,
        "title": "Jane Doe Net Worth",
        "content_html": "<h2>Net Worth</h2><p>Jane Doe net worth is estimated at five million. Last Updated: 2026 | Sources: Wikipedia</p>" * 50,
        "meta_description": "Jane Doe net worth overview and career details for readers.",
    }

    with patch(
        "app.services.blog_content_audit_service._discover_posts",
        new=AsyncMock(return_value=items),
    ), patch(
        "app.services.blog_content_audit_service.fetch_wp_content_for_audit",
        new=AsyncMock(return_value=wp_response),
    ), patch(
        "app.services.blog_content_audit_service.build_gap_matrix",
        new=AsyncMock(return_value=[{"topic": "Early Life", "status": "missing", "evidence": "test"}]),
    ), patch(
        "app.services.blog_content_audit_service.resolve_api_key_optional",
        return_value=None,
    ):
        await run_blog_content_audit(db_session, user.id, run.id)

    db_session.refresh(run)
    assert run.status == "completed"
    assert run.site_summary is not None
    assert run.site_summary.get("posts_audited") == 2

    audits = db_session.query(BlogContentAudit).filter(BlogContentAudit.run_id == run.id).all()
    assert len(audits) == 2
    assert all(a.overall_score is not None for a in audits)
