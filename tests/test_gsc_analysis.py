"""Unit tests for GSC analysis rules."""

from app.services.gsc_analysis_service import (
    PageAggregate,
    compute_impact_score,
    detect_issues,
    expected_ctr,
    normalize_page_url,
)


def test_expected_ctr_by_position():
    assert expected_ctr(1) == 0.28
    assert expected_ctr(5) == 0.06
    assert expected_ctr(15) == 0.01


def test_normalize_page_url():
    assert normalize_page_url("https://www.example.com/blog/post/") == "example.com/blog/post"
    assert normalize_page_url("/blog/post") == "/blog/post"


def test_flags_low_ctr():
    current = {
        ("video editing", "https://example.com/services"): PageAggregate(),
    }
    agg = current[("video editing", "https://example.com/services")]
    agg.impressions = 4200
    agg.clicks = 75
    agg.ctr = 75 / 4200
    agg.position = 3.0

    issues = detect_issues(current, {}, {}, {}, {}, lambda _: set())
    low_ctr = [i for i in issues if i["action_type"] == "low_ctr"]
    assert len(low_ctr) >= 1
    assert low_ctr[0]["metric_snapshot"]["expected_ctr"] == expected_ctr(3)


def test_flags_striking_distance():
    current = {
        ("wedding video", "https://example.com/page"): PageAggregate(),
    }
    agg = current[("wedding video", "https://example.com/page")]
    agg.impressions = 200
    agg.clicks = 5
    agg.ctr = 0.025
    agg.position = 11.0

    issues = detect_issues(current, {}, {}, {}, {}, lambda _: set())
    assert any(i["action_type"] == "striking_distance" for i in issues)


def test_flags_content_decay():
    current = {
        ("cost query", "https://example.com/pricing"): PageAggregate(),
    }
    prior = {
        ("cost query", "https://example.com/pricing"): PageAggregate(),
    }
    cur = current[("cost query", "https://example.com/pricing")]
    cur.impressions = 300
    cur.position = 18.0
    cur.clicks = 10
    cur.ctr = 10 / 300

    prv = prior[("cost query", "https://example.com/pricing")]
    prv.impressions = 280
    prv.position = 4.2

    issues = detect_issues(current, prior, {}, {}, {}, lambda _: set())
    decay = [i for i in issues if i["action_type"] == "content_decay"]
    assert len(decay) == 1
    assert decay[0]["metric_snapshot"]["prior_position"] == 4.2


def test_flags_zero_clicks():
    current = {
        ("checklist", "https://example.com/checklist"): PageAggregate(),
    }
    agg = current[("checklist", "https://example.com/checklist")]
    agg.impressions = 640
    agg.clicks = 0
    agg.ctr = 0.0
    agg.position = 7.0

    issues = detect_issues(current, {}, {}, {}, {}, lambda _: set())
    assert any(i["action_type"] == "zero_clicks" for i in issues)


def test_impact_score_capped_at_100():
    score = compute_impact_score("low_ctr", 50000)
    assert score <= 100.0
