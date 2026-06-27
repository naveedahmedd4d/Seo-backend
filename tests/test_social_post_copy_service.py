"""Tests for per-platform social post copy builders."""

from app.services.social_post_copy_service import (
    PublishCopyContext,
    build_instagram_caption,
    build_x_text,
    build_linkedin_text,
    build_pinterest_description,
    build_facebook_message,
    build_reddit_title,
    publish_context_from_history,
)


def _ctx(**kwargs) -> PublishCopyContext:
    defaults = {
        "headline": "Breaking News Headline",
        "short_description": "Short card blurb.",
        "story_caption": "Longer story caption for feeds.",
        "hashtags": ["news", "trending"],
        "source_url": "https://example.com/article",
    }
    defaults.update(kwargs)
    return PublishCopyContext(**defaults)


def test_instagram_uses_story_and_hashtags():
    caption = build_instagram_caption(_ctx())
    assert "Longer story caption" in caption
    assert "#news" in caption
    assert "#trending" in caption


def test_instagram_truncates_at_2200():
    long_story = "x" * 2500
    caption = build_instagram_caption(_ctx(story_caption=long_story))
    assert len(caption) <= 2200


def test_x_respects_280_char_limit():
    text = build_x_text(
        _ctx(
            headline="H" * 200,
            short_description="D" * 200,
            hashtags=["tag"] * 20,
            source_url="https://example.com/long-path",
        )
    )
    assert len(text) <= 280


def test_x_includes_headline_and_hashtags():
    text = build_x_text(_ctx())
    assert "Breaking News Headline" in text
    assert "#news" in text


def test_linkedin_story_without_duplicate_url_in_text():
    text = build_linkedin_text(_ctx())
    assert "Longer story caption" in text
    assert "example.com" not in text


def test_pinterest_description_story_plus_tags():
    desc = build_pinterest_description(_ctx())
    assert "Longer story caption" in desc
    assert "#news" in desc


def test_facebook_message_headline_and_story():
    msg = build_facebook_message(_ctx())
    assert "Breaking News Headline" in msg
    assert "Longer story caption" in msg


def test_reddit_title_is_headline():
    assert build_reddit_title(_ctx()) == "Breaking News Headline"


class _FakeHistory:
    headline = "Headline"
    description = "Short"
    story_description = "Story"
    hashtags = ["a", "b"]
    news_results = [{"link": "https://news.test/item"}]


def test_publish_context_from_history():
    ctx = publish_context_from_history(_FakeHistory())
    assert ctx.headline == "Headline"
    assert ctx.source_url == "https://news.test/item"
    assert ctx.hashtags == ["a", "b"]
