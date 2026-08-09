"""Tests for sanitizing blog body content (TOC, companion links, external links)."""

from app.services.blog_consistency import (
    append_site_cta_html,
    append_site_cta_markdown,
    sanitize_blog_body,
    strip_companion_article_links,
    strip_external_markdown_links,
    strip_table_of_contents,
)


def test_strip_bold_toc_block():
    body = (
        "Opening paragraph.\n\n"
        "**Table of Contents**\n\n"
        "- [Introduction](#introduction)\n"
        "- [Quick Facts](#quick-facts)\n\n"
        "## Introduction\n\n"
        "Main content here."
    )
    cleaned = strip_table_of_contents(body)
    assert "Table of Contents" not in cleaned
    assert "## Introduction" in cleaned
    assert cleaned.startswith("Opening paragraph.")


def test_strip_h2_toc_block():
    body = (
        "## Table of Contents\n\n"
        "- [Early Life](#early-life)\n\n"
        "## Early Life\n\n"
        "Born in London."
    )
    cleaned = strip_table_of_contents(body)
    assert "Table of Contents" not in cleaned
    assert "## Early Life" in cleaned


def test_strip_net_worth_companion_cta():
    body = (
        "| Team | Atlanta Braves |\n\n"
        "For more information on Jorge Mateo's net worth, visit "
        "[/jorge-mateo-net-worth](/jorge-mateo-net-worth).\n\n"
        "## Early Life\n\n"
        "Born in Santo Domingo."
    )
    cleaned = strip_companion_article_links(body)
    assert "net worth" not in cleaned.lower() or "visit" not in cleaned.lower()
    assert "jorge-mateo-net-worth" not in cleaned
    assert "## Early Life" in cleaned
    assert "Atlanta Braves" in cleaned


def test_sanitize_blog_body_removes_toc_and_companion_links():
    body = (
        "**Table of Contents**\n\n"
        "- [Intro](#intro)\n\n"
        "For more information on Jane Doe's net worth, visit [/jane-doe-net-worth](/jane-doe-net-worth).\n\n"
        "## Intro\n\n"
        "Hello."
    )
    cleaned = sanitize_blog_body(body)
    assert "Table of Contents" not in cleaned
    assert "jane-doe-net-worth" not in cleaned
    assert "## Intro" in cleaned


def test_strip_external_markdown_links_keeps_text():
    body = (
        "According to [Wikipedia](https://en.wikipedia.org/wiki/Stephen_Curry), "
        "he was born in Akron."
    )
    cleaned = strip_external_markdown_links(body)
    assert "https://" not in cleaned
    assert "Wikipedia" in cleaned
    assert "he was born in Akron" in cleaned


def test_strip_external_markdown_links_preserves_internal_paths():
    body = "See [our guide](/stephen-curry-net-worth) for more."
    cleaned = strip_external_markdown_links(body)
    assert "](/stephen-curry-net-worth)" in cleaned


def test_sanitize_blog_body_strips_external_links():
    body = "## Career\n\nStats from [ESPN](https://www.espn.com/nba) show strong performance."
    cleaned = sanitize_blog_body(body)
    assert "https://" not in cleaned
    assert "ESPN" in cleaned


def test_append_site_cta_markdown_adds_relative_link():
    body = "## Conclusion\n\nSummary paragraph."
    result = append_site_cta_markdown(body)
    assert result.endswith("For more insights, explore our [website](/).\n")
    assert "[website](/)" in result


def test_append_site_cta_markdown_skips_duplicate():
    body = "For more insights, explore our [website](/)."
    assert append_site_cta_markdown(body) == body


def test_append_site_cta_html_uses_site_url():
    body = "<p>Article body</p>"
    result = append_site_cta_html(body, href="https://example.com")
    assert '<a href="https://example.com">website</a>' in result
    assert "For more insights" in result


def test_append_site_cta_survives_sanitize_blog_body():
    body = append_site_cta_markdown("## Intro\n\nContent here.")
    cleaned = sanitize_blog_body(body)
    assert "[website](/)" in cleaned
    assert "For more insights" in cleaned
