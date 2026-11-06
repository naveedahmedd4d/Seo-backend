"""Unit tests for weekly content helpers (CSV import + calendar seed)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.user import User
from app.services.auth_service import get_password_hash
from app.services.international_events_service import _load_seed, upcoming_week_window
from datetime import date, timedelta

from app.services.weekly_content_service import (
    create_social_topic,
    delete_keyword_plan_item,
    delete_social_topic,
    get_or_create_settings,
    get_weekly_readiness,
    import_keywords_csv,
    iso_week_key,
    week_bucket_for_date,
)
from app.models.weekly_content import CalendarEvent, KeywordPlanItem, WeeklySocialItem


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
def test_user(db_session):
    user = User(
        email="weekly@example.com",
        password_hash=get_password_hash("password123"),
        full_name="Weekly Tester",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_iso_week_key_format():
    key = iso_week_key()
    assert key.startswith("20")
    assert "-W" in key


def test_seed_observances_loaded():
    seed = _load_seed()
    assert len(seed) >= 50
    assert all("month" in row and "day" in row and "title" in row for row in seed)


def test_upcoming_week_window_is_seven_days():
    start, end = upcoming_week_window()
    # Inclusive window: today .. today+6 → 7 calendar days.
    assert (end - start).days == 6


def test_import_keywords_csv_with_headers(db_session, test_user):
    content = b"keyword,category,priority\nai agents,Technology & AI,10\nremote work,Business,20\n"
    import_row, created = import_keywords_csv(
        db_session,
        test_user.id,
        filename="keywords.csv",
        content=content,
    )
    assert created == 2
    assert import_row.row_count == 2
    rows = (
        db_session.query(KeywordPlanItem)
        .filter(KeywordPlanItem.user_id == test_user.id)
        .order_by(KeywordPlanItem.priority.asc())
        .all()
    )
    assert [r.keyword for r in rows] == ["ai agents", "remote work"]
    assert rows[0].category == "Technology & AI"
    assert rows[0].priority == 10


def test_import_keywords_csv_plain_lines(db_session, test_user):
    content = b"keyword one\nkeyword two\n"
    _, created = import_keywords_csv(
        db_session,
        test_user.id,
        filename="plain.csv",
        content=content,
    )
    assert created == 2


def test_delete_keyword_plan_item(db_session, test_user):
    content = b"keyword,category,priority\nkeep me,Tech,1\ndelete me,Tech,2\n"
    import_keywords_csv(
        db_session,
        test_user.id,
        filename="keywords.csv",
        content=content,
    )
    rows = (
        db_session.query(KeywordPlanItem)
        .filter(KeywordPlanItem.user_id == test_user.id)
        .order_by(KeywordPlanItem.priority.asc())
        .all()
    )
    assert len(rows) == 2
    to_delete = rows[1]
    assert delete_keyword_plan_item(db_session, test_user.id, to_delete.id) is True
    remaining = (
        db_session.query(KeywordPlanItem)
        .filter(KeywordPlanItem.user_id == test_user.id)
        .all()
    )
    assert [r.keyword for r in remaining] == ["keep me"]
    assert delete_keyword_plan_item(db_session, test_user.id, to_delete.id) is False


def test_default_weekly_quotas(db_session, test_user):
    row = get_or_create_settings(db_session, test_user.id)
    assert row.max_social_per_week == 7
    assert row.blogs_per_week == 2
    assert row.goal1_enabled is False
    assert row.goal2_enabled is False


def test_weekly_readiness_checklist(db_session, test_user):
    data = get_weekly_readiness(db_session, test_user.id)
    assert data["iso_week"] == iso_week_key()
    assert data["max_social_per_week"] == 7
    assert data["blogs_per_week"] == 2
    assert data["ready"] is False
    ids = {c["id"] for c in data["checks"]}
    assert "cron_secret" in ids
    assert "goal1_enabled" in ids
    assert "keyword_queue" in ids


def test_week_bucket_for_date():
    today = date(2026, 11, 4)  # Wednesday ISO week
    monday = date(2026, 11, 2)
    next_monday = date(2026, 11, 9)
    later = date(2026, 11, 20)
    past = date(2026, 10, 20)
    assert week_bucket_for_date(monday, today=today) == "current"
    assert week_bucket_for_date(next_monday, today=today) == "next"
    assert week_bucket_for_date(later, today=today) == "upcoming"
    assert week_bucket_for_date(past, today=today) == "past"


def test_create_and_delete_social_topic(db_session, test_user):
    publish = date.today() + timedelta(days=3)
    created = create_social_topic(
        db_session,
        test_user.id,
        topic="  Launch AI tips  ",
        publish_date=publish,
    )
    assert created["topic"] == "Launch AI tips"
    assert created["iso_week"] == iso_week_key(publish)
    assert created["week_bucket"] in {"current", "next", "upcoming"}
    assert created["status"] == "queued"

    event = db_session.query(CalendarEvent).filter(CalendarEvent.id == created["event_id"]).one()
    assert event.source == "custom"
    assert event.external_id.startswith(f"user-{test_user.id}-")

    item = db_session.query(WeeklySocialItem).filter(WeeklySocialItem.id == created["item_id"]).one()
    assert item.iso_week == created["iso_week"]

    assert delete_social_topic(db_session, test_user.id, created["event_id"]) is True
    assert db_session.query(CalendarEvent).filter(CalendarEvent.id == created["event_id"]).first() is None
    assert (
        db_session.query(WeeklySocialItem).filter(WeeklySocialItem.id == created["item_id"]).first()
        is None
    )
