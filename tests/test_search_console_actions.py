"""Search Console action API tests."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.gsc_property import GscProperty
from app.models.gsc_index_audit_run import GscIndexAuditRun
from app.models.seo_action_item import SeoActionItem
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


def test_oauth_setup_returns_redirect_uri(client, auth_headers):
    headers, _ = auth_headers
    resp = client.get("/api/search-console/oauth/setup", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["redirect_uri"].endswith("/api/search-console/oauth/callback")
    assert data["has_app_credentials"] is False
    assert data["client_id"] is None


def test_oauth_connect_saves_credentials_and_returns_auth_url(client, auth_headers):
    headers, _ = auth_headers
    resp = client.post(
        "/api/search-console/oauth/connect",
        json={
            "client_id": "test-client-id.apps.googleusercontent.com",
            "client_secret": "test-client-secret-value",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "authorization_url" in data
    assert "accounts.google.com" in data["authorization_url"]
    assert "test-client-id.apps.googleusercontent.com" in data["authorization_url"]

    setup = client.get("/api/search-console/oauth/setup", headers=headers)
    assert setup.status_code == 200
    setup_data = setup.json()
    assert setup_data["has_app_credentials"] is True
    assert setup_data["client_id"] == "test-client-id.apps.googleusercontent.com"


def test_oauth_connect_rejects_placeholder_credentials(client, auth_headers):
    headers, _ = auth_headers
    resp = client.post(
        "/api/search-console/oauth/connect",
        json={"client_id": "your-client-id", "client_secret": "your-client-secret"},
        headers=headers,
    )
    assert resp.status_code == 400


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


def test_index_audit_requires_wordpress_link(client, auth_headers, db_session):
    from unittest.mock import patch

    headers, user = auth_headers
    prop = GscProperty(user_id=user.id, site_url="sc-domain:example.com", display_name="example.com")
    db_session.add(prop)
    db_session.commit()
    db_session.refresh(prop)

    with patch("app.api.search_console.is_gsc_connected", return_value=True):
        resp = client.post(
            "/api/search-console/index-audit",
            json={"property_id": prop.id},
            headers=headers,
        )
    assert resp.status_code == 400
    assert "WordPress" in resp.json()["detail"]


def test_index_audit_start_and_status(client, auth_headers, db_session):
    from unittest.mock import patch

    headers, user = auth_headers
    wp = WordPressSite(
        user_id=user.id,
        name="WP",
        url="https://example.com",
        username="admin",
        api_key=encrypt_api_key("secret"),
    )
    db_session.add(wp)
    db_session.commit()
    db_session.refresh(wp)

    prop = GscProperty(
        user_id=user.id,
        site_url="sc-domain:example.com",
        display_name="example.com",
        wordpress_site_id=wp.id,
    )
    db_session.add(prop)
    db_session.commit()
    db_session.refresh(prop)

    with patch("app.api.search_console.is_gsc_connected", return_value=True), patch(
        "app.api.search_console.bg_run_index_audit"
    ):
        start = client.post(
            "/api/search-console/index-audit",
            json={"property_id": prop.id},
            headers=headers,
        )
    assert start.status_code == 200
    assert start.json()["status"] in ("pending", "running")
    assert start.json()["property_id"] == prop.id

    status = client.get(
        f"/api/search-console/index-audit/status?property_id={prop.id}",
        headers=headers,
    )
    assert status.status_code == 200
    assert status.json()["property_id"] == prop.id


def test_list_not_indexed_actions(client, auth_headers, db_session):
    headers, user = auth_headers
    prop = GscProperty(user_id=user.id, site_url="sc-domain:example.com", display_name="example.com")
    db_session.add(prop)
    db_session.commit()
    db_session.refresh(prop)

    action = SeoActionItem(
        property_id=prop.id,
        user_id=user.id,
        action_type="not_indexed",
        severity="warning",
        page="https://example.com/unindexed/",
        query=None,
        metric_snapshot={
            "verdict": "FAIL",
            "coverage_state": "Crawled - currently not indexed",
            "diagnosis": {"root_cause": "thin_content", "explanation": "Too short"},
        },
        suggestion="Too short",
        impact_score=50.0,
        status="pending_review",
    )
    db_session.add(action)
    db_session.commit()

    resp = client.get(
        f"/api/search-console/actions?property_id={prop.id}&action_type=not_indexed",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["action_type"] == "not_indexed"


def test_approve_not_indexed_triggers_applying(client, auth_headers, db_session):
    from unittest.mock import patch

    headers, user = auth_headers
    prop = GscProperty(user_id=user.id, site_url="sc-domain:example.com", display_name="example.com")
    db_session.add(prop)
    db_session.commit()
    db_session.refresh(prop)

    action = SeoActionItem(
        property_id=prop.id,
        user_id=user.id,
        action_type="not_indexed",
        severity="warning",
        page="https://example.com/unindexed/",
        metric_snapshot={"wp_post_id": 1, "wp_post_type": "post"},
        suggestion="Fix it",
        impact_score=50.0,
        status="pending_review",
    )
    db_session.add(action)
    db_session.commit()
    db_session.refresh(action)

    with patch("app.api.search_console.bg_apply_index_fix"):
        resp = client.patch(
            f"/api/search-console/actions/{action.id}",
            json={"status": "approved"},
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json()["fix_status"] == "applying"


def test_indexing_queue_lists_ready_items(client, auth_headers, db_session):
    headers, user = auth_headers
    prop = GscProperty(user_id=user.id, site_url="sc-domain:example.com", display_name="example.com")
    db_session.add(prop)
    db_session.commit()
    db_session.refresh(prop)

    action = SeoActionItem(
        property_id=prop.id,
        user_id=user.id,
        action_type="not_indexed",
        severity="warning",
        page="https://example.com/queue/",
        status="approved",
        fix_status="applied",
        indexing_status="ready_for_manual",
        gsc_inspect_url="https://search.google.com/search-console/inspect?resource_id=x&url=y",
        impact_score=50.0,
    )
    db_session.add(action)
    db_session.commit()

    resp = client.get(
        f"/api/search-console/indexing-queue?property_id={prop.id}",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["indexing_status"] == "ready_for_manual"
