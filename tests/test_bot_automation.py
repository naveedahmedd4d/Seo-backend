from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.bot_automation import AutomationBot, AutomationExecution, AutomationNodeExecution
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.services.auth_service import create_access_token, get_password_hash
from app.services.bot_automation_service import (
    create_execution,
    default_workflow_definition,
    next_run_at,
    utcnow,
    validate_workflow,
)


engine = create_engine(
    "sqlite://",
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
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def make_user_and_site(db, email: str):
    user = User(email=email, password_hash=get_password_hash("password123"), full_name="Bot Tester")
    db.add(user)
    db.flush()
    site = WordPressSite(
        user_id=user.id,
        name=f"{email} site",
        url="https://example.com",
        api_key="encrypted-test-key",
        username="editor",
    )
    db.add(site)
    db.commit()
    db.refresh(user)
    db.refresh(site)
    token = create_access_token({"sub": user.email})
    return user, site, {"Authorization": f"Bearer {token}"}


def test_graph_validation_rejects_cycles():
    graph = default_workflow_definition()
    graph["edges"].append({"id": "cycle", "source": "social", "target": "trigger"})
    with pytest.raises(ValueError, match="cycle"):
        validate_workflow(graph)


def test_schedule_next_run_calculations():
    now = utcnow()
    assert next_run_at({"mode": "manual"}, now) is None
    daily = next_run_at({"mode": "daily", "time": "09:00"}, now)
    weekly = next_run_at({"mode": "weekly", "weekday": now.weekday(), "time": "09:00"}, now)
    assert now < daily <= now + timedelta(days=1)
    assert now < weekly <= now + timedelta(days=7)
    assert next_run_at({"mode": "interval", "interval": 30, "unit": "minutes"}, now) == now + timedelta(minutes=30)


def test_bots_are_isolated_by_user_and_website(client, db_session):
    owner, owner_site, owner_headers = make_user_and_site(db_session, "bot-owner@test.com")
    _, other_site, other_headers = make_user_and_site(db_session, "bot-other@test.com")

    created = client.post(
        "/api/bot-automation/bots",
        headers=owner_headers,
        json={"name": "Owner bot", "wordpress_site_id": owner_site.id},
    )
    assert created.status_code == 201
    bot_id = created.json()["id"]
    assert client.get(f"/api/bot-automation/bots/{bot_id}", headers=other_headers).status_code == 404

    wrong_site = client.post(
        "/api/bot-automation/bots",
        headers=owner_headers,
        json={"name": "Invalid bot", "wordpress_site_id": other_site.id},
    )
    assert wrong_site.status_code == 404
    assert [item["name"] for item in client.get("/api/bot-automation/bots", headers=owner_headers).json()["items"]] == ["Owner bot"]


def test_bot_can_be_created_without_wordpress_site(client, db_session):
    user = User(
        email="site-free-bot@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Site-free Bot Tester",
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token({"sub": user.email})

    response = client.post(
        "/api/bot-automation/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Generate-only bot"},
    )

    assert response.status_code == 201
    assert response.json()["wordpress_site_id"] is None
    bot = db_session.query(AutomationBot).filter_by(id=response.json()["id"]).one()
    execution = create_execution(db_session, bot)
    assert execution.wordpress_site_id is None


def test_restart_execution_reuses_successful_checkpoints(db_session):
    user, site, _ = make_user_and_site(db_session, "checkpoint@test.com")
    bot = AutomationBot(
        user_id=user.id,
        wordpress_site_id=site.id,
        name="Checkpoint bot",
        status="draft",
        workflow_definition=default_workflow_definition(),
        settings={},
        schedule_config={"mode": "manual"},
    )
    db_session.add(bot)
    db_session.commit()
    first = create_execution(db_session, bot)
    rows = db_session.query(AutomationNodeExecution).filter(
        AutomationNodeExecution.execution_id == first.id
    ).order_by(AutomationNodeExecution.position).all()
    rows[0].status = "completed"
    rows[0].output_data = {"triggered": True}
    rows[1].status = "failed"
    first.status = "failed"
    db_session.commit()

    restarted = create_execution(
        db_session,
        bot,
        start_node_id=rows[1].node_id,
        resumed_from=first,
    )
    restarted_rows = db_session.query(AutomationNodeExecution).filter(
        AutomationNodeExecution.execution_id == restarted.id
    ).order_by(AutomationNodeExecution.position).all()
    assert restarted_rows[0].status == "completed"
    assert restarted_rows[0].output_data == {"triggered": True}
    assert restarted_rows[0].usage_data == {"checkpoint_reused": True}
    assert restarted_rows[1].status == "pending"
