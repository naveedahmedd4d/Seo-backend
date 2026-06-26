"""Authority Builder service tests."""

from unittest.mock import AsyncMock, patch

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
from app.services import authority_service

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
def user_and_wp(db_session):
    user = User(
        email="authority@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Authority Tester",
    )
    db_session.add(user)
    db_session.flush()
    wp = WordPressSite(
        user_id=user.id,
        name="Test Site",
        url="https://example.com",
        api_key=encrypt_api_key("test-key"),
        username="admin",
    )
    db_session.add(wp)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(wp)
    return user, wp


@pytest.fixture()
def auth_headers(user_and_wp):
    user, _ = user_and_wp
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def test_list_sites_empty(client, auth_headers):
    response = client.get("/api/authority/sites", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_create_authority_site(client, auth_headers, user_and_wp):
    _, wp = user_and_wp
    response = client.post(
        "/api/authority/sites",
        headers=auth_headers,
        json={
            "wordpress_site_id": wp.id,
            "money_page_title": "Best Video Editing Company in Delhi",
            "money_page_focus_keyword": "best video editing company delhi",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["money_page"]["title"] == "Best Video Editing Company in Delhi"
    assert data["service_hubs"] == []
    assert data["stats"]["money_page_count"] == 1


def test_duplicate_authority_site_rejected(client, auth_headers, user_and_wp):
    _, wp = user_and_wp
    payload = {
        "wordpress_site_id": wp.id,
        "money_page_title": "Money Page",
        "money_page_focus_keyword": "money keyword",
    }
    client.post("/api/authority/sites", headers=auth_headers, json=payload)
    response = client.post("/api/authority/sites", headers=auth_headers, json=payload)
    assert response.status_code == 400


def test_create_cluster_with_default_blogs(client, auth_headers, user_and_wp):
    _, wp = user_and_wp
    site_resp = client.post(
        "/api/authority/sites",
        headers=auth_headers,
        json={
            "wordpress_site_id": wp.id,
            "money_page_title": "Money Page",
            "money_page_focus_keyword": "money keyword",
        },
    )
    site_id = site_resp.json()["id"]
    response = client.post(
        f"/api/authority/sites/{site_id}/clusters",
        headers=auth_headers,
        json={
            "title": "Wedding Video Editing Services",
            "focus_keyword": "wedding video editing services",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert len(data["service_hubs"]) == 1
    assert len(data["service_hubs"][0]["blogs"]) == 4
    assert data["stats"]["internal_links_total"] > 0
    assert data["link_health"]["blog_to_service_pct"] == 100


def test_max_five_clusters(client, auth_headers, user_and_wp):
    _, wp = user_and_wp
    site_resp = client.post(
        "/api/authority/sites",
        headers=auth_headers,
        json={
            "wordpress_site_id": wp.id,
            "money_page_title": "Money Page",
            "money_page_focus_keyword": "money keyword",
        },
    )
    site_id = site_resp.json()["id"]
    for i in range(5):
        resp = client.post(
            f"/api/authority/sites/{site_id}/clusters",
            headers=auth_headers,
            json={"title": f"Service {i}", "focus_keyword": f"service {i}"},
        )
        assert resp.status_code == 201
    resp = client.post(
        f"/api/authority/sites/{site_id}/clusters",
        headers=auth_headers,
        json={"title": "Service 6", "focus_keyword": "service 6"},
    )
    assert resp.status_code == 400


def test_rebuild_links(client, auth_headers, user_and_wp):
    _, wp = user_and_wp
    site_resp = client.post(
        "/api/authority/sites",
        headers=auth_headers,
        json={
            "wordpress_site_id": wp.id,
            "money_page_title": "Money Page",
            "money_page_focus_keyword": "money keyword",
        },
    )
    site_id = site_resp.json()["id"]
    client.post(
        f"/api/authority/sites/{site_id}/clusters",
        headers=auth_headers,
        json={"title": "Service", "focus_keyword": "service keyword"},
    )
    response = client.post(
        f"/api/authority/sites/{site_id}/rebuild-links",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["link_health"]["service_to_money_pct"] == 100


def test_user_isolation(client, db_session, user_and_wp):
    user, wp = user_and_wp
    site = authority_service.create_authority_site(
        db_session,
        user.id,
        wp.id,
        "Money Page",
        "money keyword",
    )
    other = User(
        email="other@test.com",
        password_hash=get_password_hash("password123"),
    )
    db_session.add(other)
    db_session.commit()

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token({"sub": other.email})
    with TestClient(app) as test_client:
        response = test_client.get(
            f"/api/authority/sites/{site.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
    app.dependency_overrides.clear()


def test_link_health_issues(db_session, user_and_wp):
    user, wp = user_and_wp
    site = authority_service.create_authority_site(
        db_session,
        user.id,
        wp.id,
        "Money Page",
        "money keyword",
    )
    authority_service.create_cluster(
        db_session,
        site.id,
        user.id,
        "Wedding Services",
        "wedding video editing",
    )
    site = authority_service.get_authority_site(db_session, site.id, user.id)
    _, health = authority_service.compute_stats_and_health(site)
    assert health["blog_to_service_pct"] == 100
    assert any(i["severity"] == "success" for i in health["issues"])


def test_update_money_page_content(client, auth_headers, user_and_wp):
    _, wp = user_and_wp
    site_resp = client.post(
        "/api/authority/sites",
        headers=auth_headers,
        json={
            "wordpress_site_id": wp.id,
            "money_page_title": "Money Page",
            "money_page_focus_keyword": "money keyword",
        },
    )
    site_id = site_resp.json()["id"]
    response = client.put(
        f"/api/authority/sites/{site_id}/money-page/content",
        headers=auth_headers,
        json={"body": "# Draft content", "seo_title": "SEO", "meta_description": "Meta"},
    )
    assert response.status_code == 200
    money = response.json()["money_page"]
    assert money["body"] == "# Draft content"
    assert money["status"] == "draft"
    assert money["seo_title"] == "SEO"


def test_generate_money_page_sets_generating(client, auth_headers, user_and_wp):
    _, wp = user_and_wp
    site_resp = client.post(
        "/api/authority/sites",
        headers=auth_headers,
        json={
            "wordpress_site_id": wp.id,
            "money_page_title": "Money Page",
            "money_page_focus_keyword": "money keyword",
        },
    )
    site_id = site_resp.json()["id"]
    with patch(
        "app.api.authority.bg_run_generate_money_page",
        new_callable=AsyncMock,
    ):
        response = client.post(
            f"/api/authority/sites/{site_id}/money-page/generate",
            headers=auth_headers,
            json={"include_research": False},
        )
    assert response.status_code == 200
    assert response.json()["money_page"]["is_generating"] is True

