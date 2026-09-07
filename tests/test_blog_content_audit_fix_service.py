"""Tests for blog content audit fix service."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.blog_content_audit import BlogContentAudit
from app.models.blog_content_audit_fix import BlogContentAuditFix
from app.models.blog_content_audit_run import BlogContentAuditRun
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.services.auth_service import get_password_hash
from app.services.blog_content_audit_fix_service import edit_fix, preview_combined, skip_fix
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
def fix_setup(db_session):
    user = User(email="fix@test.com", password_hash=get_password_hash("secret123"))
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
        word_target_met=False,
        wp_post_id=1,
    )
    db_session.add(audit)
    db_session.commit()
    fix = BlogContentAuditFix(
        audit_id=audit.id,
        fix_type="append_sources_footer",
        target_field="content",
        title="Footer",
        severity="high",
        status="proposed",
        before_text="<p>Body</p>",
        after_proposed="<p>Body</p><p><strong>Last Updated:</strong> 2026-01-01</p>",
        diff_segments=[{"type": "insert", "text": " footer"}],
    )
    db_session.add(fix)
    db_session.commit()
    return user, run, audit, fix


def test_edit_fix_recomputes_diff(db_session, fix_setup):
    user, run, audit, fix = fix_setup
    updated = edit_fix(
        db_session,
        run_id=run.id,
        audit_id=audit.id,
        fix_id=fix.id,
        user_id=user.id,
        after_proposed="<p>Body edited</p>",
    )
    assert updated.status == "edited"
    assert updated.after_proposed == "<p>Body edited</p>"
    assert len(updated.diff_segments or []) >= 1


def test_skip_fix(db_session, fix_setup):
    user, run, audit, fix = fix_setup
    updated = skip_fix(
        db_session,
        run_id=run.id,
        audit_id=audit.id,
        fix_id=fix.id,
        user_id=user.id,
    )
    assert updated.status == "skipped"


def test_preview_combined(db_session, fix_setup):
    user, run, audit, fix = fix_setup
    result = preview_combined(
        db_session,
        run_id=run.id,
        audit_id=audit.id,
        user_id=user.id,
        fix_ids=[fix.id],
    )
    assert "merged_content" in result
    assert result["word_count_after"] >= result["word_count_before"]
