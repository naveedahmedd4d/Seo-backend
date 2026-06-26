"""Search Console action API tests."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.gsc_property import GscProperty
from app.models.seo_action_item import SeoActionItem
from app.models.user import User
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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_headers(db_session):
    user = User(email="gsc@test.com", password_hash=get_password_hash("secret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}, user


@pytest.fixture()
def property_and_action(db_session, auth_headers):
    _, user = auth_headers
    prop = GscProperty(user_id=user.id, site_url="sc-domain:example.com", display_name="example.com")
    db_session.add(prop)
    db_session.commit()
    db_session.refresh(prop)

    action = SeoActionItem(
        property_id=prop.id,
        user_id=user.id,
        action_type="low_ctr",
        severity="critical",
        page="https://example.com/page",
        query="test query",
        metric_snapshot={"position": 3, "ctr": 0.018, "expected_ctr": 0.11, "impressions": 4200},
        suggestion="Fix meta",
        impact_score=94.0,
        status="pending_review",
    )
    db_session.add(action)
    db_session.commit()
    db_session.refresh(action)
    return prop, action


def test_oauth_status_not_connected(client, auth_headers):
    headers, _ = auth_headers
    resp = client.get("/api/search-console/oauth/status", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["connected"] is False


def test_list_actions(client, auth_headers, property_and_action):
    headers, _ = auth_headers
    prop, action = property_and_action
    resp = client.get(
        f"/api/search-console/actions?property_id={prop.id}",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == action.id
    assert data["items"][0]["impact_score"] == 94.0


def test_approve_action(client, auth_headers, property_and_action):
    headers, _ = auth_headers
    prop, action = property_and_action
    resp = client.patch(
        f"/api/search-console/actions/{action.id}",
        json={"status": "approved"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_dismiss_action_bulk(client, auth_headers, property_and_action):
    headers, _ = auth_headers
    _, action = property_and_action
    resp = client.post(
        "/api/search-console/actions/bulk",
        json={"ids": [action.id], "status": "dismissed"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
