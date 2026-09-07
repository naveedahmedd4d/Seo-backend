"""Tests for multi-signal thin content analysis."""

from app.services.website_audit_thin_content import (
    analyze_thin_content,
    compute_thin_score,
    is_utility_or_home_url,
)


def _signals(**kwargs):
    base = {
        "word_count": 800,
        "h2_count": 3,
        "paragraph_count": 5,
        "list_count": 1,
        "table_count": 0,
        "media_count": 1,
        "text_to_html_ratio": 0.25,
        "avg_paragraph_words": 40.0,
    }
    base.update(kwargs)
    return base


def test_utility_and_home_detection():
    assert is_utility_or_home_url("https://example.com/")
    assert is_utility_or_home_url("https://example.com")
    assert is_utility_or_home_url("https://example.com/contact")
    assert is_utility_or_home_url("https://example.com/privacy-policy/")
    assert not is_utility_or_home_url("https://example.com/blog/my-post")


def test_post_critical_thin():
    score, issues = analyze_thin_content(
        _signals(word_count=80, h2_count=0, paragraph_count=1),
        url="https://example.com/blog/short",
        post_type="post",
    )
    assert score < 70
    assert len(issues) == 1
    assert issues[0]["issue_code"] == "thin_content_critical"
    assert issues[0]["severity"] == "critical"
    assert issues[0]["evidence"]["word_count"] == 80


def test_post_thin():
    _, issues = analyze_thin_content(
        _signals(word_count=220, h2_count=1, paragraph_count=3),
        url="https://example.com/blog/thin",
        post_type="post",
    )
    assert len(issues) == 1
    assert issues[0]["issue_code"] == "thin_content"
    assert issues[0]["severity"] == "high"


def test_page_thresholds_lower_than_post():
    # 220 words: thin for posts (<300) but OK length for pages (>=200) with structure
    signals = _signals(word_count=220, h2_count=2, paragraph_count=3, list_count=1)
    _, post_issues = analyze_thin_content(
        signals,
        url="https://example.com/blog/x",
        post_type="post",
    )
    _, page_issues = analyze_thin_content(
        signals,
        url="https://example.com/about",
        post_type="page",
    )
    assert post_issues[0]["issue_code"] == "thin_content"
    assert page_issues == []


def test_page_passes_at_250_with_structure():
    score, issues = analyze_thin_content(
        _signals(word_count=250, h2_count=3, paragraph_count=4, list_count=1),
        url="https://example.com/services",
        post_type="page",
    )
    assert score >= 70
    assert issues == []


def test_shallow_borderline_post():
    _, issues = analyze_thin_content(
        _signals(word_count=350, h2_count=0, paragraph_count=1, list_count=0, media_count=0),
        url="https://example.com/blog/shallow",
        post_type="post",
    )
    assert len(issues) == 1
    assert issues[0]["issue_code"] == "thin_content_shallow"
    assert "no_h2" in issues[0]["evidence"]["triggers"] or "borderline_length" in issues[0]["evidence"]["triggers"]


def test_healthy_post_passes():
    score, issues = analyze_thin_content(
        _signals(word_count=900, h2_count=4, paragraph_count=8),
        url="https://example.com/blog/full",
        post_type="post",
    )
    assert score >= 85
    assert issues == []


def test_homepage_skips_unless_empty():
    score, issues = analyze_thin_content(
        _signals(word_count=120, h2_count=0, paragraph_count=1),
        url="https://example.com/",
        post_type="page",
    )
    assert issues == []

    _, empty_issues = analyze_thin_content(
        _signals(word_count=20, h2_count=0, paragraph_count=0),
        url="https://example.com/",
        post_type="page",
    )
    assert len(empty_issues) == 1
    assert empty_issues[0]["issue_code"] == "thin_content_critical"


def test_contact_utility_skip():
    _, issues = analyze_thin_content(
        _signals(word_count=90, h2_count=0, paragraph_count=1),
        url="https://example.com/contact",
        post_type="page",
    )
    assert issues == []


def test_compute_thin_score_penalties():
    high = compute_thin_score(_signals(word_count=1000, h2_count=5, paragraph_count=10))
    low = compute_thin_score(
        _signals(word_count=50, h2_count=0, paragraph_count=0, text_to_html_ratio=0.02)
    )
    assert high > low
    assert low < 50
