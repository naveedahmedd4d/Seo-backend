"""Tests for sanitizing blog body content (TOC, companion links)."""

from app.services.blog_consistency import (
    sanitize_blog_body,
    strip_companion_article_links,
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
