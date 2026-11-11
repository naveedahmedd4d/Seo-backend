"""Blog → social / outreach share toggles and per-platform caption helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.openrouter_service import (
    generate_platform_social_captions,
)
from app.services.weekly_content_service import platforms_for_blog_share


def test_platforms_for_blog_share_empty_when_both_off():
    settings = SimpleNamespace(share_blogs_to_social=False, share_blogs_to_outreach=False)
    db = MagicMock()
    with patch(
        "app.services.weekly_content_service.get_user_social_accounts",
        return_value=[
            SimpleNamespace(platform="facebook"),
            SimpleNamespace(platform="devto"),
        ],
    ):
        assert platforms_for_blog_share(db, 1, settings) == []


def test_platforms_for_blog_share_social_only():
    settings = SimpleNamespace(share_blogs_to_social=True, share_blogs_to_outreach=False)
    db = MagicMock()
    with patch(
        "app.services.weekly_content_service.get_user_social_accounts",
        return_value=[
            SimpleNamespace(platform="facebook"),
            SimpleNamespace(platform="x"),
            SimpleNamespace(platform="devto"),
        ],
    ):
        assert platforms_for_blog_share(db, 1, settings) == ["facebook", "x"]


def test_platforms_for_blog_share_outreach_only():
    settings = SimpleNamespace(share_blogs_to_social=False, share_blogs_to_outreach=True)
    db = MagicMock()
    with patch(
        "app.services.weekly_content_service.get_user_social_accounts",
        return_value=[
            SimpleNamespace(platform="linkedin"),
            SimpleNamespace(platform="hashnode"),
            SimpleNamespace(platform="blogger"),
        ],
    ):
        assert platforms_for_blog_share(db, 1, settings) == ["hashnode", "blogger"]


def test_platforms_for_blog_share_both_on():
    settings = SimpleNamespace(share_blogs_to_social=True, share_blogs_to_outreach=True)
    db = MagicMock()
    with patch(
        "app.services.weekly_content_service.get_user_social_accounts",
        return_value=[
            SimpleNamespace(platform="instagram"),
            SimpleNamespace(platform="devto"),
        ],
    ):
        assert platforms_for_blog_share(db, 1, settings) == ["instagram", "devto"]


@pytest.mark.asyncio
async def test_generate_platform_social_captions_distinct_and_capped():
    with patch(
        "app.services.openrouter_service.has_usable_llm_key",
        return_value=True,
    ), patch(
        "app.services.openrouter_service.generate_content",
        new_callable=AsyncMock,
        return_value='{"facebook":"FB unique take on the story.","x":"X unique take — shorter."}',
    ):
        captions = await generate_platform_social_captions(
            topic="AI agents",
            headline="Agents ship",
            facts="Vendors launched agents this week.",
            platforms=["facebook", "x"],
        )
    assert captions["facebook"] != captions["x"]
    assert len(captions["x"]) <= 280
    assert len(captions["facebook"]) <= 500
