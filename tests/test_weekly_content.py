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
    delete_weekly_social_item,
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


def test_delete_weekly_social_item_keeps_event(db_session, test_user):
    publish = date.today() + timedelta(days=2)
    created = create_social_topic(
        db_session,
        test_user.id,
        topic="Retry failed auto post",
        publish_date=publish,
    )
    assert delete_weekly_social_item(db_session, test_user.id, created["item_id"]) is True
    assert (
        db_session.query(WeeklySocialItem).filter(WeeklySocialItem.id == created["item_id"]).first()
        is None
    )
    assert db_session.query(CalendarEvent).filter(CalendarEvent.id == created["event_id"]).first() is not None
    assert delete_weekly_social_item(db_session, test_user.id, created["item_id"]) is False


# --- schedule gate -------------------------------------------------------

from datetime import datetime, timezone as _tz

from app.models.weekly_content import WeeklyContentSettings
from app.services.weekly_content_service import is_run_due


def _settings(**kw) -> WeeklyContentSettings:
    row = WeeklyContentSettings(user_id=1)
    row.run_frequency = kw.get("run_frequency", "weekly")
    row.cron_weekday = kw.get("cron_weekday", 0)
    row.cron_time_utc = kw.get("cron_time_utc", "09:00")
    row.last_run_iso_week = kw.get("last_run_iso_week")
    row.last_run_date = kw.get("last_run_date")
    return row


def _utc(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_tz.utc)


def test_weekly_fires_on_chosen_weekday_and_time():
    # Tuesday 2026-11-10, 11:30 UTC == 16:30 GMT+5.
    row = _settings(cron_weekday=1, cron_time_utc="11:30")
    assert is_run_due(row, now=_utc(2026, 11, 10, 11, 30)) is True


def test_weekly_does_not_fire_before_chosen_time():
    row = _settings(cron_weekday=1, cron_time_utc="11:30")
    assert is_run_due(row, now=_utc(2026, 11, 10, 11, 0)) is False


def test_weekly_does_not_fire_on_other_weekday():
    row = _settings(cron_weekday=1, cron_time_utc="11:30")
    # Wednesday, same time.
    assert is_run_due(row, now=_utc(2026, 11, 11, 11, 30)) is False


def test_weekly_fires_within_grace_after_cold_start():
    row = _settings(cron_weekday=1, cron_time_utc="11:30")
    assert is_run_due(row, now=_utc(2026, 11, 10, 12, 30)) is True


def test_weekly_skips_long_after_slot():
    row = _settings(cron_weekday=1, cron_time_utc="11:30")
    assert is_run_due(row, now=_utc(2026, 11, 10, 20, 0)) is False


def test_weekly_does_not_refire_same_iso_week():
    row = _settings(cron_weekday=1, cron_time_utc="11:30", last_run_iso_week="2026-W46")
    assert is_run_due(row, now=_utc(2026, 11, 10, 11, 30)) is False


def test_daily_ignores_weekday():
    row = _settings(run_frequency="daily", cron_weekday=0, cron_time_utc="11:30")
    # Wednesday — weekly would skip, daily should fire.
    assert is_run_due(row, now=_utc(2026, 11, 11, 11, 30)) is True


def test_daily_fires_once_per_day():
    row = _settings(
        run_frequency="daily",
        cron_time_utc="11:30",
        last_run_date=_utc(2026, 11, 11, 11, 30).date(),
    )
    assert is_run_due(row, now=_utc(2026, 11, 11, 12, 0)) is False
    # Next day's slot is due again.
    assert is_run_due(row, now=_utc(2026, 11, 12, 11, 30)) is True


def test_malformed_time_falls_back_to_0900():
    row = _settings(cron_weekday=1, cron_time_utc="not-a-time")
    assert is_run_due(row, now=_utc(2026, 11, 10, 9, 0)) is True
