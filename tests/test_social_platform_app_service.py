"""Tests for per-user social platform app credentials."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.services.social_platform_app_service import (
    get_pinterest_redirect_uri,
    upsert_user_platform_app,
    validate_pinterest_app_credentials,
    user_has_pinterest_app,
)
from app.models.user import User
from app.services.auth_service import get_password_hash

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


def test_validate_rejects_placeholder():
    assert validate_pinterest_app_credentials("your_app_id", "secret123456") is not None
    assert validate_pinterest_app_credentials("1536123", "your_app_secret") is not None


def test_validate_rejects_email_as_client_id():
    assert validate_pinterest_app_credentials("user@example.com", "secret12345678") is not None


def test_upsert_and_has_credentials(db_session):
    user = User(email="p@test.com", password_hash=get_password_hash("pass"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    upsert_user_platform_app(db_session, user.id, "pinterest", "1536123456", "pina_real_secret_value")
    assert user_has_pinterest_app(db_session, user.id) is True


def test_redirect_uri_contains_callback():
    uri = get_pinterest_redirect_uri()
    assert "/api/social-accounts/pinterest/callback" in uri
