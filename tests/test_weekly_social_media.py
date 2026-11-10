"""Tests for weekly Goal 1 image attach (LLM pick + card render + fallbacks)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.openrouter_service import (
    _parse_image_choice_index,
    select_social_background_image,
)
from app.services.weekly_social_media import (
    _candidate_image_url,
    _fallback_index,
    ensure_weekly_social_media,
)


MINIMAL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_parse_image_choice_index():
    assert _parse_image_choice_index("3", 5) == 2
    assert _parse_image_choice_index("I pick 7", 8) == 6
    assert _parse_image_choice_index("nope", 4) == 0
    assert _parse_image_choice_index("99", 4) == 0


def test_fallback_index_prefers_third_when_available():
    assert _fallback_index(0) == 0
    assert _fallback_index(1) == 0
    assert _fallback_index(2) == 0
    assert _fallback_index(3) == 2
    assert _fallback_index(12) == 2


def test_candidate_image_url_prefers_original():
    assert _candidate_image_url({"original": "https://cdn.example/a.jpg", "link": "https://x"}) == (
        "https://cdn.example/a.jpg"
    )
    assert _candidate_image_url({"link": "https://cdn.example/b.jpg"}) == "https://cdn.example/b.jpg"
    assert _candidate_image_url({}) == ""


@pytest.mark.asyncio
async def test_select_social_background_image_uses_llm_number():
    images = [
        {"title": "a", "source": "s", "original": "https://a"},
        {"title": "b", "source": "s", "original": "https://b"},
        {"title": "c", "source": "s", "original": "https://c"},
    ]
    with patch(
        "app.services.openrouter_service.generate_content",
        new_callable=AsyncMock,
        return_value="2",
    ):
        idx = await select_social_background_image("World Science Day", "Headline", "Caption", images)
    assert idx == 1


@pytest.mark.asyncio
async def test_select_social_background_image_falls_back_to_third_on_error():
    images = [{"title": str(i), "original": f"https://x/{i}"} for i in range(5)]
    with patch(
        "app.services.openrouter_service.generate_content",
        new_callable=AsyncMock,
        side_effect=RuntimeError("llm down"),
    ):
        idx = await select_social_background_image("topic", "h", "d", images)
    assert idx == 2


@pytest.mark.asyncio
async def test_ensure_weekly_social_media_saves_rendered_card():
    history = SimpleNamespace(media_filename=None, publish_status="draft", query="AI agents")
    db = MagicMock()
    user = SimpleNamespace(id=1)

    candidates = [
        {"title": "one", "original": "https://img.example/1.jpg"},
        {"title": "two", "original": "https://img.example/2.jpg"},
        {"title": "three", "original": "https://img.example/3.jpg"},
    ]

    with patch(
        "app.services.weekly_social_media._search_background_candidates",
        new_callable=AsyncMock,
        return_value=candidates,
    ), patch(
        "app.services.weekly_social_media.select_social_background_image",
        new_callable=AsyncMock,
        return_value=1,
    ), patch(
        "app.services.weekly_social_media._render_social_card_png",
        new_callable=AsyncMock,
        return_value=MINIMAL_PNG_B64,
    ), patch(
        "app.services.weekly_social_media.save_png_base64",
        return_value="card.png",
    ), patch(
        "app.services.weekly_social_media.public_image_url",
        return_value="https://api.example/api/social-post/media/card.png",
    ):
        url = await ensure_weekly_social_media(
            db,
            user,
            history,
            query="AI agents",
            headline="Agents are here",
            description="Short caption",
        )

    assert url == "https://api.example/api/social-post/media/card.png"
    assert history.media_filename == "card.png"
    assert history.publish_status == "media_ready"
    db.commit.assert_called()


@pytest.mark.asyncio
async def test_ensure_weekly_social_media_falls_back_to_raw_url_when_render_fails():
    history = SimpleNamespace(media_filename=None, publish_status="draft", query="Diabetes Day")
    db = MagicMock()
    user = SimpleNamespace(id=1)
    candidates = [{"title": "photo", "original": "https://cdn.example/raw.jpg"}]

    with patch(
        "app.services.weekly_social_media._search_background_candidates",
        new_callable=AsyncMock,
        return_value=candidates,
    ), patch(
        "app.services.weekly_social_media.select_social_background_image",
        new_callable=AsyncMock,
        return_value=0,
    ), patch(
        "app.services.weekly_social_media._render_social_card_png",
        new_callable=AsyncMock,
        return_value=None,
    ):
        url = await ensure_weekly_social_media(
            db,
            user,
            history,
            query="Diabetes Day",
            headline="Headline",
            description="Caption",
        )

    assert url == "https://cdn.example/raw.jpg"
    assert history.media_filename is None


@pytest.mark.asyncio
async def test_ensure_weekly_social_media_returns_none_when_no_image():
    history = SimpleNamespace(media_filename=None, publish_status="draft", query="x")
    db = MagicMock()
    user = SimpleNamespace(id=1)

    with patch(
        "app.services.weekly_social_media._search_background_candidates",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.weekly_social_media._render_social_card_png",
        new_callable=AsyncMock,
        return_value=None,
    ):
        url = await ensure_weekly_social_media(
            db,
            user,
            history,
            query="x",
            headline="h",
            description="d",
        )

    assert url is None


@pytest.mark.asyncio
async def test_generate_and_publish_skips_facebook_without_image():
    """When media resolution fails, Facebook is skipped with an explicit error."""
    from app.services import weekly_content_service as wcs

    db = MagicMock()
    user = SimpleNamespace(id=9)

    with patch(
        "app.services.llm_settings_service.resolve_llm_credentials",
        return_value=object(),
    ), patch(
        "app.services.openrouter_service.set_llm_context",
        return_value="tok",
    ), patch(
        "app.services.openrouter_service.reset_llm_context",
    ), patch(
        "app.services.openrouter_service.generate_news_headline",
        new_callable=AsyncMock,
        return_value="Headline",
    ), patch(
        "app.services.openrouter_service.generate_social_post_description",
        new_callable=AsyncMock,
        return_value="Description",
    ), patch(
        "app.services.openrouter_service.generate_social_post_story_description",
        new_callable=AsyncMock,
        return_value="Story",
    ), patch(
        "app.services.openrouter_service.generate_social_post_hashtags",
        new_callable=AsyncMock,
        return_value=["#a"],
    ), patch(
        "app.services.weekly_social_media.ensure_weekly_social_media",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.services.social_post_link_service.resolve_facebook_destination_url",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.services.social_post_platform_publish.publish_to_platform",
        new_callable=AsyncMock,
    ) as mock_publish, patch(
        "app.services.social_post_publish_service.record_publications",
    ), patch(
        "app.services.social_post_publish_service.finalize_history_status",
    ):
        _history, results = await wcs._generate_and_publish_social(
            db,
            user,
            query="World Science Day",
            source_title="World Science Day",
            source_snippet="UN observance",
            source_url=None,
            platforms=["facebook"],
            settings_snapshot={"source": "test"},
        )

    mock_publish.assert_not_awaited()
    assert len(results) == 1
    assert results[0]["platform"] == "facebook"
    assert results[0]["success"] is False
    assert "No image available" in results[0]["error"]
