"""Tests for per-platform social post copy builders."""

from app.services.social_post_copy_service import (
    PublishCopyContext,
    build_instagram_caption,
    build_x_text,
    build_linkedin_text,
    build_pinterest_description,
    build_mastodon_text,
    build_facebook_message,
    build_reddit_title,
    build_platform_copy,
    publish_context_from_history,
)


def _ctx(**kwargs) -> PublishCopyContext:
    defaults = {
        "headline": "Breaking **News** Headline",
        "short_description": "Short card blurb.",
        "story_caption": "Longer story caption for feeds.",
        "hashtags": ["news", "trending"],
        "source_url": "https://example.com/article",
    }
    defaults.update(kwargs)
    return PublishCopyContext(**defaults)


def test_instagram_image_card_uses_story_and_hashtags():
    caption = build_instagram_caption(_ctx())
    assert "Longer story caption" in caption
    assert "#news" in caption
    assert "#trending" in caption
    assert "Breaking" not in caption
    assert "example.com" not in caption


def test_instagram_falls_back_to_short_description():
    caption = build_instagram_caption(_ctx(story_caption=None))
    assert "Short card blurb." in caption
    assert "#news" in caption


def test_instagram_truncates_at_2200():
    long_story = "x" * 2500
    caption = build_instagram_caption(_ctx(story_caption=long_story))
    assert len(caption) <= 2200


def test_x_image_card_story_and_hashtags_respects_280_char_limit():
    text = build_x_text(_ctx(hashtags=["tag"] * 50, story_caption="S" * 300))
    assert len(text) <= 280
    assert "Breaking" not in text
    assert "example.com" not in text


def test_x_includes_story_and_hashtags():
    text = build_x_text(_ctx())
    assert "Longer story caption" in text
    assert "#news" in text


def test_linkedin_story_without_url_in_text():
    text = build_linkedin_text(_ctx())
    assert "Longer story caption" in text
    assert "example.com" not in text


def test_pinterest_description_story_plus_tags():
    desc = build_pinterest_description(_ctx())
    assert "Longer story caption" in desc
    assert "#news" in desc


def test_mastodon_story_and_hashtags_no_source_url():
    text = build_mastodon_text(_ctx())
    assert "Longer story caption" in text
    assert "#news" in text
    assert "example.com" not in text
    assert "Breaking" not in text


def test_pinterest_title_strips_highlight_markup():
    copy = build_platform_copy("pinterest", _ctx())
    assert copy["title"] == "Breaking News Headline"
    assert "Longer story caption" in (copy.get("description") or "")


def test_facebook_message_headline_story_and_hashtags():
    msg = build_facebook_message(_ctx())
    assert "Breaking News Headline" in msg
    assert "**" not in msg
    assert "Longer story caption" in msg
    assert msg.endswith("#news #trending")


def test_facebook_message_preserves_prefixed_hashtags():
    msg = build_facebook_message(_ctx(hashtags=["#MLB", "Baseball", " "]))
    assert msg.endswith("#MLB #Baseball")


def test_facebook_message_rejects_full_article_markdown():
    article = "## Intro\n\n" + ("Paragraph. " * 80) + "\n\n## Next\n\n- one\n- two\n"
    msg = build_facebook_message(
        _ctx(story_caption=article, short_description="A short social blurb about the project.")
    )
    assert "## Intro" not in msg
    assert "A short social blurb about the project." in msg
    assert len(msg) < 800


def test_reddit_title_is_headline():
    assert build_reddit_title(_ctx()) == "Breaking **News** Headline"


class _FakeHistory:
    headline = "Headline"
    description = "Short"
    story_description = "Story"
    hashtags = ["a", "b"]
    news_results = [{"link": "https://news.test/item"}]


def test_publish_context_from_history():
    ctx = publish_context_from_history(_FakeHistory())
    assert ctx.headline == "Headline"
    assert ctx.story_caption == "Story"
    assert ctx.source_url == "https://news.test/item"
    assert ctx.hashtags == ["a", "b"]
