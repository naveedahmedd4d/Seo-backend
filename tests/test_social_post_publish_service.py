"""Tests for social post publish orchestration."""

import base64
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.social_post_history import SocialPostHistory
from app.models.user import User
from app.schemas.social_post import SocialPostPublishResultItem
from app.services.social_post_publish_service import (
    HistoryNotFoundError,
    MediaRequiredError,
    ensure_media_saved,
    finalize_history_status,
    load_history_post,
    publish_history_post,
    record_publications,
)

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

MINIMAL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _seed_history(db, user_id: int = 1) -> SocialPostHistory:
    user = User(id=user_id, email="u@test.com", password_hash="x")
    db.add(user)
    history = SocialPostHistory(
        user_id=user_id,
        query="test query",
        headline="Test Headline",
        description="Short desc",
        story_description="Story caption",
        hashtags=["news"],
        news_results=[{"link": "https://example.com/a"}],
        publish_status="draft",
    )
    db.add(history)
    db.commit()
    db.refresh(history)
    return history


def test_load_history_post_not_found(db_session):
    with pytest.raises(HistoryNotFoundError):
        load_history_post(db_session, 1, 999)


def test_ensure_media_requires_base64_when_missing(db_session):
    history = _seed_history(db_session)
    with pytest.raises(MediaRequiredError):
        ensure_media_saved(db_session, history, None)


@patch("app.services.social_post_publish_service.save_png_base64")
def test_ensure_media_saves_and_updates_status(mock_save, db_session):
    mock_save.return_value = "test-file.png"
    history = _seed_history(db_session)
    filename, url = ensure_media_saved(db_session, history, MINIMAL_PNG_B64)
    assert filename == "test-file.png"
    assert url
    db_session.refresh(history)
    assert history.media_filename == "test-file.png"
    assert history.publish_status == "media_ready"


def test_finalize_history_status_all_success(db_session):
    history = _seed_history(db_session)
    results = [
        SocialPostPublishResultItem(platform="x", success=True),
        SocialPostPublishResultItem(platform="mastodon", success=True),
    ]
    status = finalize_history_status(db_session, history, results)
    assert status == "published"
    db_session.refresh(history)
    assert history.published_at is not None


def test_finalize_history_status_partial(db_session):
    history = _seed_history(db_session)
    results = [
        SocialPostPublishResultItem(platform="x", success=True),
        SocialPostPublishResultItem(platform="mastodon", success=False, error="fail"),
    ]
    status = finalize_history_status(db_session, history, results)
    assert status == "partial"


def test_record_publications_upserts(db_session):
    history = _seed_history(db_session)
    record_publications(
        db_session,
        1,
        history.id,
        [SocialPostPublishResultItem(platform="x", success=True, external_id="1")],
    )
    record_publications(
        db_session,
        1,
        history.id,
        [SocialPostPublishResultItem(platform="x", success=False, error="retry failed")],
    )
    from app.models.social_post_publication import SocialPostPublication

    rows = db_session.query(SocialPostPublication).filter_by(history_id=history.id).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"


@pytest.mark.asyncio
@patch("app.services.social_post_publish_service.publish_to_platforms", new_callable=AsyncMock)
@patch("app.services.social_post_publish_service.ensure_media_saved")
async def test_publish_history_post_orchestration(mock_ensure, mock_publish, db_session):
    history = _seed_history(db_session)
    mock_ensure.return_value = ("file.png", "https://api.test/media/file.png")
    mock_publish.return_value = [
        SocialPostPublishResultItem(platform="x", success=True, external_id="tw1"),
    ]

    results, updated, media_url = await publish_history_post(
        db_session,
        1,
        history.id,
        ["x"],
        image_base64=MINIMAL_PNG_B64,
    )
    assert len(results) == 1
    assert results[0].success
    assert updated.publish_status == "published"
    assert media_url == "https://api.test/media/file.png"
    mock_ensure.assert_called_once()
    mock_publish.assert_called_once()


@pytest.mark.asyncio
async def test_publish_to_platforms_batches_upload_post(db_session, monkeypatch, tmp_path):
    from app.services import social_post_publish_service as sps
    from app.services import upload_post_service as ups

    monkeypatch.setattr(ups.settings, "upload_post_api_key", "key")
    monkeypatch.setattr(sps.settings, "upload_post_api_key", "key")
    monkeypatch.setattr(sps.settings, "upload_post_pinterest_board_id", "board-1")

    history = _seed_history(db_session)
    media_file = tmp_path / "card.png"
    media_file.write_bytes(b"png-bytes")
    history.media_filename = media_file.name
    history.publish_status = "media_ready"
    db_session.commit()

    up_results = [
        SocialPostPublishResultItem(platform="instagram", success=True, external_id="ig1"),
        SocialPostPublishResultItem(platform="x", success=True, external_id="x1"),
    ]
    native_result = SocialPostPublishResultItem(
        platform="mastodon", success=True, external_id="m1"
    )

    with (
        patch.object(sps, "resolve_media_path", return_value=str(media_file)),
        patch.object(sps, "upload_photos", new=AsyncMock(return_value=up_results)) as mock_up,
        patch.object(
            sps, "publish_to_platform", new=AsyncMock(return_value=native_result)
        ) as mock_native,
    ):
        results = await sps.publish_to_platforms(
            db_session,
            1,
            history,
            ["instagram", "mastodon", "x"],
        )

    assert [r.platform for r in results] == ["instagram", "mastodon", "x"]
    assert all(r.success for r in results)
    mock_up.assert_awaited_once()
    assert set(mock_up.await_args.args[0]) == {"instagram", "x"}
    mock_native.assert_awaited_once()
    assert mock_native.await_args.args[2] == "mastodon"
