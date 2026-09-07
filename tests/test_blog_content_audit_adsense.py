"""Tests for AdSense risk module."""

from app.services.blog_content_audit_adsense import (
    adsense_quality_score_from_risks,
    compute_site_adsense_risk,
    detect_page_adsense_risks,
)
from app.services.blog_content_audit_parser import parse_post_content


def test_net_worth_thin_critical_risk():
    html = "<p>Net worth is estimated at one million. Early life and career details.</p>"
    parsed = parse_post_content(
        html=html,
        title="Star Net Worth",
        url="https://example.com/star-net-worth",
    )
    risks = detect_page_adsense_risks(parsed, deterministic_findings=[], gap_missing_count=0)
    codes = {r["code"] for r in risks}
    assert "net_worth_thin" in codes or "shallow_word_count" in codes
    score = adsense_quality_score_from_risks(risks)
    assert score < 80


def test_site_adsense_risk_rollup():
    high = [{"severity": "critical"}, {"severity": "critical"}]
    low = [{"severity": "low"}]
    assert compute_site_adsense_risk([high, high, low, low, low]) == "high"
    assert compute_site_adsense_risk([low, low, low, low, low, low, low, low, low, low]) == "low"
