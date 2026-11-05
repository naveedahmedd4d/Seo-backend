"""Unit tests for weekly content helpers (CSV import + calendar seed)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.user import User
from app.services.auth_service import get_password_hash
from app.services.international_events_service import _load_seed, upcoming_week_window
from app.services.weekly_content_service import import_keywords_csv, iso_week_key
from app.models.weekly_content import KeywordPlanItem


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
    assert (end - start).days == 7


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
