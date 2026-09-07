"""API ownership / smoke tests for Website Audit."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
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


def _site(db_session, user: User, name: str = "Blog"):
    site = WordPressSite(
        user_id=user.id,
        name=name,
        url="https://example.com",
        username="admin",
        api_key=encrypt_api_key("app-password"),
    )
    db_session.add(site)
    db_session.commit()
    db_session.refresh(site)
    return site


def test_list_sites(client, db_session):
    user = _user(db_session, "audit@test.com")
    _site(db_session, user)
    resp = client.get("/api/website-audit/sites", headers=_headers(user))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Blog"


def test_start_run_unknown_site_404(client, db_session):
    user = _user(db_session, "audit2@test.com")
    resp = client.post(
        "/api/website-audit/runs",
        headers=_headers(user),
        json={"wordpress_site_id": 99999, "max_pages": 10},
    )
    assert resp.status_code == 404


def test_cannot_access_other_users_site(client, db_session):
    owner = _user(db_session, "owner@test.com")
    other = _user(db_session, "other@test.com")
    site = _site(db_session, owner)

    resp = client.post(
        "/api/website-audit/runs",
        headers=_headers(other),
        json={"wordpress_site_id": site.id, "max_pages": 5},
    )
    assert resp.status_code == 404


def test_get_run_not_found(client, db_session):
    user = _user(db_session, "audit3@test.com")
    resp = client.get("/api/website-audit/runs/12345", headers=_headers(user))
    assert resp.status_code == 404


def test_start_run_creates_pending(client, db_session, monkeypatch):
    user = _user(db_session, "audit4@test.com")
    site = _site(db_session, user)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.api.website_audit.bg_run_website_audit",
        _noop,
    )

    resp = client.post(
        "/api/website-audit/runs",
        headers=_headers(user),
        json={"wordpress_site_id": site.id, "max_pages": 25},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["wordpress_site_id"] == site.id
    assert body["status"] == "pending"
    assert body["scope_json"]["max_pages"] == 25

    listed = client.get(
        f"/api/website-audit/runs?wordpress_site_id={site.id}",
        headers=_headers(user),
    )
    assert listed.status_code == 200
    assert len(listed.json()) >= 1
