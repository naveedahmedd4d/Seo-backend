"""Cluster keywords API tests (main + nested subs)."""

from datetime import datetime

import pytest
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
        email="cluster@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Cluster Tester",
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def test_add_cluster_mains(client, auth_headers, db_session):
    response = client.post(
        "/api/trending/keywords/cluster",
        headers=auth_headers,
        json={
            "keywords": ["Tyla", "Chappell Roan"],
            "category": "Celebrities & Public Figures",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    rows = db_session.query(TrendingKeywords).all()
    assert len(rows) == 2
    assert all(r.source == "cluster" for r in rows)
    assert all(r.parent_id is None for r in rows)


def test_duplicate_cluster_main_skipped(client, auth_headers):
    payload = {"keywords": ["Duplicate Pillar"], "category": "Sports"}
    first = client.post("/api/trending/keywords/cluster", headers=auth_headers, json=payload)
    assert first.status_code == 200
    second = client.post("/api/trending/keywords/cluster", headers=auth_headers, json=payload)
    assert second.status_code == 409


def test_add_subs_under_main(client, auth_headers, db_session):
    create = client.post(
        "/api/trending/keywords/cluster",
        headers=auth_headers,
        json={"keywords": ["Tyla"], "category": "Celebrities & Public Figures"},
    )
    assert create.status_code == 200
    main_id = create.json()["keywords"][0]["id"]

    subs = client.post(
        f"/api/trending/keywords/{main_id}/subs",
        headers=auth_headers,
        json={"keywords": ["Tyla career", "Tyla personal life"]},
    )
    assert subs.status_code == 200
    assert subs.json()["count"] == 2

    listed = client.get(
        "/api/trending/keywords?source=cluster",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    mains = listed.json()["keywords"]
    assert len(mains) == 1
    assert mains[0]["keyword"] == "Tyla"
    assert mains[0]["parent_id"] is None
    assert {s["keyword"] for s in mains[0]["subs"]} == {
        "Tyla career",
        "Tyla personal life",
    }
    assert all(s["parent_id"] == main_id for s in mains[0]["subs"])
    assert all(s["category"] == "Celebrities & Public Figures" for s in mains[0]["subs"])


def test_reject_subs_under_non_main(client, auth_headers, db_session):
    manual = TrendingKeywords(keyword="Not Cluster", data={}, source="manual")
    db_session.add(manual)
    db_session.commit()

    response = client.post(
        f"/api/trending/keywords/{manual.id}/subs",
        headers=auth_headers,
        json={"keywords": ["Child"]},
    )
    assert response.status_code == 400


def test_reject_subs_under_sub(client, auth_headers):
    create = client.post(
        "/api/trending/keywords/cluster",
        headers=auth_headers,
        json={"keywords": ["Main Pillar"]},
    )
    main_id = create.json()["keywords"][0]["id"]
    subs = client.post(
        f"/api/trending/keywords/{main_id}/subs",
        headers=auth_headers,
        json={"keywords": ["Sub One"]},
    )
    sub_id = subs.json()["keywords"][0]["id"]

    nested = client.post(
        f"/api/trending/keywords/{sub_id}/subs",
        headers=auth_headers,
        json={"keywords": ["Too Deep"]},
    )
    assert nested.status_code == 400


def test_duplicate_sub_skipped(client, auth_headers):
    create = client.post(
        "/api/trending/keywords/cluster",
        headers=auth_headers,
        json={"keywords": ["Main"]},
    )
    main_id = create.json()["keywords"][0]["id"]
    payload = {"keywords": ["Same Sub"]}
    first = client.post(
        f"/api/trending/keywords/{main_id}/subs",
        headers=auth_headers,
        json=payload,
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/trending/keywords/{main_id}/subs",
        headers=auth_headers,
        json=payload,
    )
    assert second.status_code == 409


def test_cluster_completed_only_filters_mains(client, auth_headers, db_session):
    pending = TrendingKeywords(keyword="Pending Main", data={}, source="cluster")
    done = TrendingKeywords(
        keyword="Done Main",
        data={},
        source="cluster",
        bio_blog_workflow_id=10,
    )
    db_session.add_all([pending, done])
    db_session.commit()
    db_session.refresh(pending)

    sub = TrendingKeywords(
        keyword="Sub Of Pending",
        data={},
        source="cluster",
        parent_id=pending.id,
        bio_blog_workflow_id=99,
    )
    db_session.add(sub)
    db_session.commit()

    pending_resp = client.get(
        "/api/trending/keywords?source=cluster&completed_only=false",
        headers=auth_headers,
    )
    assert pending_resp.status_code == 200
    assert [k["keyword"] for k in pending_resp.json()["keywords"]] == ["Pending Main"]
    assert len(pending_resp.json()["keywords"][0]["subs"]) == 1

    completed_resp = client.get(
        "/api/trending/keywords?source=cluster&completed_only=true",
        headers=auth_headers,
    )
    assert completed_resp.status_code == 200
    assert [k["keyword"] for k in completed_resp.json()["keywords"]] == ["Done Main"]


def test_delete_main_cascades_subs(client, auth_headers, db_session):
    create = client.post(
        "/api/trending/keywords/cluster",
        headers=auth_headers,
        json={"keywords": ["Cascade Main"]},
    )
    main_id = create.json()["keywords"][0]["id"]
    client.post(
        f"/api/trending/keywords/{main_id}/subs",
        headers=auth_headers,
        json={"keywords": ["Cascade Sub"]},
    )
    assert db_session.query(TrendingKeywords).count() == 2

    deleted = client.delete(
        f"/api/trending/keywords/{main_id}",
        headers=auth_headers,
    )
    assert deleted.status_code == 200
    assert db_session.query(TrendingKeywords).count() == 0


def test_cluster_excludes_manual(client, auth_headers, db_session):
    db_session.add(
        TrendingKeywords(
            keyword="Manual Row",
            data={},
            source="manual",
            fetched_at=datetime.utcnow(),
        )
    )
    db_session.add(
        TrendingKeywords(
            keyword="Cluster Row",
            data={},
            source="cluster",
            fetched_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    cluster = client.get("/api/trending/keywords?source=cluster", headers=auth_headers)
    assert [k["keyword"] for k in cluster.json()["keywords"]] == ["Cluster Row"]
