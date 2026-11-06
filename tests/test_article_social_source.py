"""Building a social post from a published article instead of a news query."""

import pytest

from app.services import article_social_service as svc


def test_strips_html_and_collapses_whitespace():
    html = "<h2>Title</h2><p>First para.</p>\n\n<p>Second &amp; third.</p>"
    assert svc.html_to_text(html) == "Title First para. Second & third."


def test_drops_script_and_style_bodies():
    html = "<p>Keep</p><script>var x = 'drop me';</script><style>.a{color:red}</style>"
    assert svc.html_to_text(html) == "Keep"


def test_truncates_to_a_prompt_sized_excerpt():
    body = "<p>" + ("word " * 5000) + "</p>"
    out = svc.html_to_text(body, limit=200)
    assert len(out) <= 200


def test_first_body_image_found():
    html = '<p>hi</p><img src="https://ex.com/a.jpg" alt="x"><img src="https://ex.com/b.jpg">'
    assert svc.first_body_image(html) == "https://ex.com/a.jpg"


def test_first_body_image_handles_single_quotes_and_none():
    assert svc.first_body_image("<img src='https://ex.com/c.png'>") == "https://ex.com/c.png"
    assert svc.first_body_image("<p>no images</p>") is None
    assert svc.first_body_image("") is None


def test_image_search_keyword_prefers_target_keyword():
    assert svc.image_keyword(title="Some Title", target_keyword="seo tips") == "seo tips"


def test_image_search_keyword_falls_back_to_title():
    assert svc.image_keyword(title="Some Title", target_keyword=None) == "Some Title"
    assert svc.image_keyword(title="Some Title", target_keyword="  ") == "Some Title"


def test_article_excerpt_prefers_content_over_title():
    text = svc.build_prompt_source(title="T", content_html="<p>Body here.</p>")
    assert "Body here." in text
    assert "T" in text


def test_build_prompt_source_survives_empty_body():
    """An article with no body must still yield something to write from."""
    text = svc.build_prompt_source(title="Only a title", content_html="")
    assert "Only a title" in text


def test_as_news_result_shape_matches_what_generators_read():
    item = svc.as_news_result(
        title="My Post", content_html="<p>Body text.</p>",
        url="https://ex.com/p", site="ex.com",
    )
    # the generators read exactly these keys
    assert set(item) >= {"title", "link", "source", "snippet", "date"}
    assert item["title"] == "My Post"
    assert item["snippet"] == "Body text."
    assert item["source"] == "ex.com"


# --- endpoint branching -------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_image_prefers_featured(monkeypatch):
    from app.api import social_post as api

    async def fake_media(site, mid):
        return "https://ex.com/featured.jpg"

    monkeypatch.setattr(api, "get_wp_media", fake_media)
    url, src = await api._resolve_article_image(
        object(), {"featured_media": 7}, '<img src="https://ex.com/body.jpg">',
        "Title", "kw", "serp-key",
    )
    assert (url, src) == ("https://ex.com/featured.jpg", "featured")


@pytest.mark.asyncio
async def test_resolve_image_falls_back_to_body_image(monkeypatch):
    from app.api import social_post as api

    async def no_media(site, mid):
        return None

    monkeypatch.setattr(api, "get_wp_media", no_media)
    url, src = await api._resolve_article_image(
        object(), {"featured_media": 0}, '<p>x</p><img src="https://ex.com/body.jpg">',
        "Title", "kw", "serp-key",
    )
    assert (url, src) == ("https://ex.com/body.jpg", "body")


@pytest.mark.asyncio
async def test_resolve_image_falls_back_to_keyword_search(monkeypatch):
    """No featured media and no body image -> search Google Images."""
    from app.api import social_post as api
    seen = {}

    async def no_media(site, mid):
        return None

    async def fake_search(keyword, num_results=10, api_key=None):
        seen["keyword"] = keyword
        return [{"original": "https://ex.com/search.jpg"}]

    monkeypatch.setattr(api, "get_wp_media", no_media)
    monkeypatch.setattr(api, "search_images", fake_search)
    url, src = await api._resolve_article_image(
        object(), {}, "<p>no images here</p>", "Some Title", "seo tips", "serp-key",
    )
    assert (url, src) == ("https://ex.com/search.jpg", "search")
    assert seen["keyword"] == "seo tips", "searches the target keyword"


@pytest.mark.asyncio
async def test_resolve_image_returns_none_when_everything_fails(monkeypatch):
    """No image anywhere must still be survivable -- copy is the point."""
    from app.api import social_post as api

    async def no_media(site, mid):
        return None

    async def boom(keyword, num_results=10, api_key=None):
        raise RuntimeError("serpapi down")

    monkeypatch.setattr(api, "get_wp_media", no_media)
    monkeypatch.setattr(api, "search_images", boom)
    url, src = await api._resolve_article_image(
        object(), {}, "<p>x</p>", "T", None, "serp-key",
    )
    assert (url, src) == (None, None)
