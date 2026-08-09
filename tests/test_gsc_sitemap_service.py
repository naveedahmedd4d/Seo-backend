"""Unit tests for GSC sitemap service."""

from app.services.gsc_sitemap_service import build_gsc_inspect_url, sitemap_feedpath


def test_build_gsc_inspect_url():
    url = build_gsc_inspect_url("sc-domain:example.com", "https://example.com/post/")
    assert "search.google.com/search-console/inspect" in url
    assert "resource_id=" in url
    assert "url=" in url


def test_sitemap_feedpath():
    assert sitemap_feedpath("https://example.com/sitemap_index.xml") == "sitemap_index.xml"
