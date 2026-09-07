"""Tests for blog content audit deterministic checks."""

from app.services.blog_content_audit_deterministic import compute_deterministic_audit
from app.services.blog_content_audit_parser import parse_post_content


def test_word_target_flagged_for_short_net_worth():
    html = "<h2>Net Worth</h2><p>Short post about net worth is estimated at one million.</p>"
    parsed = parse_post_content(
        html=html,
        title="Celebrity Net Worth",
        url="https://example.com/celebrity-net-worth",
    )
    result = compute_deterministic_audit(parsed, missing_gaps=2, site_slugs=["celebrity-net-worth"])
    assert result["word_target_met"] is False
    depth_findings = [f for f in result["findings"] if f["category"] == "content_depth"]
    assert any("word target" in f["message"].lower() for f in depth_findings)
    assert result["scores"]["content_depth"] < 80


def test_sources_missing_eeat_penalty():
    html = "<h2>Career</h2><p>They are the richest person ever with no citation.</p>" * 30
    parsed = parse_post_content(
        html=html,
        title="Someone Net Worth",
        url="https://example.com/someone-net-worth",
        meta_description="A long meta description " * 5,
    )
    result = compute_deterministic_audit(parsed)
    eeat_findings = [f for f in result["findings"] if f["category"] == "eeat"]
    assert len(eeat_findings) >= 1
    assert result["scores"]["eeat"] < 90
