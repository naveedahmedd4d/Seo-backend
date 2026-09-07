"""Tests for blog content audit parser."""

from app.services.blog_content_audit_parser import (
    content_hash_from_text,
    guess_article_type,
    parse_post_content,
)


SAMPLE_HTML = """
<h2>Early Life</h2>
<p>Jane Doe was born in 1990 and grew up in Los Angeles with a passion for acting.</p>
<h2>Net Worth</h2>
<p>Jane Doe net worth is estimated at five million dollars according to various sources.</p>
<p>Last Updated: 2026-01-01 | Sources: Wikipedia</p>
<img src="/a.jpg" alt="Jane Doe" />
<img src="/b.jpg" />
<a href="/other-post">Related</a>
<a href="https://example.com">External</a>
"""


def test_guess_article_type_net_worth():
    assert guess_article_type("Jane Doe Net Worth 2026", "jane-doe-net-worth", "https://x.com/jane-doe-net-worth") == "net_worth"


def test_guess_article_type_biography():
    assert guess_article_type("Jane Doe Biography", "jane-doe-biography", "https://x.com/jane-doe-biography") == "biography"


def test_parse_post_content_word_count_and_headings():
    parsed = parse_post_content(
        html=SAMPLE_HTML,
        title="Jane Doe Net Worth 2026",
        url="https://example.com/jane-doe-net-worth",
        meta_description="Jane Doe net worth and career overview.",
    )
    assert parsed.article_type == "net_worth"
    assert parsed.word_target == 2500
    assert parsed.word_count > 20
    assert len(parsed.headings) >= 2
    assert parsed.images_total == 2
    assert parsed.images_missing_alt == 1
    assert parsed.internal_links >= 1
    assert parsed.has_last_updated_footer is True
    assert parsed.content_hash == content_hash_from_text(parsed.plain_text)


def test_content_hash_stable():
    a = content_hash_from_text("Hello   World")
    b = content_hash_from_text("hello world")
    assert a == b
