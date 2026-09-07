"""Tests for blog content audit fix planner."""

from app.services.blog_content_audit_fix_planner import plan_fixes


def test_plan_meta_and_footer():
    plans = plan_fixes(
        findings=[{"category": "seo", "message": "Missing meta description", "severity": "high"}],
        gap_matrix=[],
        adsense_risks=[],
        audit_payload={},
        word_target_met=False,
        word_count=500,
        word_target=2500,
        article_type="net_worth",
        has_meta=False,
        meta_length=0,
        missing_alt_count=0,
        has_sources_footer=False,
    )
    types = {p.fix_type for p in plans}
    assert "add_meta_description" in types
    assert "append_sources_footer" in types
    assert "expand_content" in types


def test_plan_missing_sections_from_gaps():
    plans = plan_fixes(
        findings=[],
        gap_matrix=[
            {"topic": "Early Life", "status": "missing"},
            {"topic": "Career", "status": "covered"},
            {"topic": "Awards", "status": "missing"},
        ],
        adsense_risks=[],
        audit_payload={},
        word_target_met=True,
        word_count=2600,
        word_target=2500,
        article_type="biography",
        has_meta=True,
        meta_length=120,
        missing_alt_count=0,
        has_sources_footer=True,
    )
    types = {p.fix_type for p in plans}
    assert "add_missing_sections" in types
    section = next(p for p in plans if p.fix_type == "add_missing_sections")
    assert len(section.payload.get("topics", [])) == 2
