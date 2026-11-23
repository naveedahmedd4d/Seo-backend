"""Tests for shared outreach / backlink platform set."""

from app.services.outreach_platforms import OUTREACH_PLATFORMS, is_outreach_platform


def test_outreach_set_has_six_platforms():
    assert len(OUTREACH_PLATFORMS) == 6
    for platform in (
        "devto",
        "hashnode",
        "blogger",
        "tumblr",
        "wordpress_com",
        "telegraph",
    ):
        assert platform in OUTREACH_PLATFORMS


def test_is_outreach_platform_normalizes_case():
    assert is_outreach_platform("DevTo")
    assert is_outreach_platform(" wordpress_com ")
    assert not is_outreach_platform("facebook")
    assert not is_outreach_platform(None)
