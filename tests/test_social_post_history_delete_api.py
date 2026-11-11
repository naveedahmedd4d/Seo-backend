"""API tests for deleting saved social posts from history."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.social_post_history import SocialPostHistory
from app.models.social_post_publication import SocialPostPublication
from app.models.user import User

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


def _auth_headers(client: TestClient, email: str = "del@test.com") -> dict[str, str]:
    client.post(
        "/api/auth/register",
        json={"email": email, "password": "securepass", "full_name": "Del User"},
    )
    login = client.post(
        "/api/auth/login",
        data={"username": email, "password": "securepass"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _seed_history(db, user_id: int, *, query: str = "q") -> int:
    row = SocialPostHistory(
        user_id=user_id,
        query=query,
        headline="Headline",
        description="desc",
        publish_status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def test_delete_history_removes_the_saved_post(client, db_session):
    # Arrange
    headers = _auth_headers(client)
    user = db_session.query(User).filter_by(email="del@test.com").first()
    history_id = _seed_history(db_session, user.id)

    # Act
    response = client.delete(f"/api/social-post/history/{history_id}", headers=headers)

    # Assert
    assert response.status_code == 204
    assert db_session.query(SocialPostHistory).filter_by(id=history_id).first() is None
    listing = client.get("/api/social-post/history", headers=headers)
    assert listing.status_code == 200
    assert listing.json() == []


def test_delete_history_also_removes_publication_rows(client, db_session):
    # Arrange
    headers = _auth_headers(client)
    user = db_session.query(User).filter_by(email="del@test.com").first()
    history_id = _seed_history(db_session, user.id)
    db_session.add(
        SocialPostPublication(
            user_id=user.id,
            history_id=history_id,
            platform="x",
            status="success",
        )
    )
    db_session.commit()

    # Act
    response = client.delete(f"/api/social-post/history/{history_id}", headers=headers)

    # Assert
    assert response.status_code == 204
    remaining = (
        db_session.query(SocialPostPublication).filter_by(history_id=history_id).count()
    )
    assert remaining == 0


def test_delete_history_returns_404_for_unknown_id(client, db_session):
    # Arrange
    headers = _auth_headers(client)

    # Act
    response = client.delete("/api/social-post/history/9999", headers=headers)

    # Assert
    assert response.status_code == 404


def test_delete_history_cannot_remove_another_users_post(client, db_session):
    # Arrange
    owner_headers = _auth_headers(client, email="owner@test.com")
    owner = db_session.query(User).filter_by(email="owner@test.com").first()
    history_id = _seed_history(db_session, owner.id)
    intruder_headers = _auth_headers(client, email="intruder@test.com")

    # Act
    response = client.delete(
        f"/api/social-post/history/{history_id}", headers=intruder_headers
    )

    # Assert
    assert response.status_code == 404
    assert db_session.query(SocialPostHistory).filter_by(id=history_id).first() is not None
