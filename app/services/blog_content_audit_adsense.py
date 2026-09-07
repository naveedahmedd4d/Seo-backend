"""AdSense low-value content risk detection for blog content audit."""

from __future__ import annotations

from typing import Any

from app.services.blog_content_audit_parser import ParsedPost

RiskFlag = dict[str, Any]


def detect_page_adsense_risks(
    parsed: ParsedPost,
    *,
    deterministic_findings: list[dict[str, Any]],
    gap_missing_count: int,
) -> list[RiskFlag]:
    """Return page-level AdSense risk flags."""
    risks: list[RiskFlag] = []
    plain_lower = parsed.plain_text.lower()

    if parsed.word_count < parsed.word_target * 0.6:
        risks.append(
            {
                "code": "shallow_word_count",
                "severity": "critical",
                "message": "Content is substantially below target word count.",
                "evidence": f"{parsed.word_count}/{parsed.word_target} words",
            }
        )

    if parsed.article_type == "net_worth" and parsed.word_count < 2000:
        risks.append(
            {
                "code": "net_worth_thin",
                "severity": "critical",
                "message": "Net-worth post below AdSense-quality length threshold.",
                "evidence": f"{parsed.word_count} words (target 2500)",
            }
        )

    template_phrases = (
        "net worth is estimated",
        "early life and career",
        "personal life",
        "business ventures",
    )
    template_hits = sum(1 for p in template_phrases if p in plain_lower)
    if template_hits >= 3 and parsed.article_type == "net_worth":
        risks.append(
            {
                "code": "templated_net_worth",
                "severity": "high",
                "message": "Templated net-worth structure may be flagged as low-value.",
                "evidence": f"{template_hits} boilerplate sections",
            }
        )

    critical_findings = [f for f in deterministic_findings if f.get("severity") == "critical"]
    if len(critical_findings) >= 2:
        risks.append(
            {
                "code": "multiple_critical_issues",
                "severity": "high",
                "message": "Multiple critical content quality issues.",
                "evidence": f"{len(critical_findings)} critical findings",
            }
        )

    if gap_missing_count >= 4:
        risks.append(
            {
                "code": "major_topic_gaps",
                "severity": "high",
                "message": "Large gaps vs top-ranking competitor topics.",
                "evidence": f"{gap_missing_count} missing topics",
            }
        )

    source_missing = not any(
        k in plain_lower for k in ("wikipedia", "according to", "sources:", "last updated")
    )
    if source_missing and parsed.word_count > 800 and parsed.article_type in ("biography", "net_worth"):
        risks.append(
            {
                "code": "unsupported_claims",
                "severity": "critical",
                "message": "Factual claims without visible source attribution.",
                "evidence": "No sources footer or attribution phrases",
            }
        )

    if parsed.duplicate_paragraph_ratio > 0.2:
        risks.append(
            {
                "code": "duplicate_content",
                "severity": "high",
                "message": "High internal paragraph repetition.",
                "evidence": f"Duplicate ratio {parsed.duplicate_paragraph_ratio:.0%}",
            }
        )

    return risks


def adsense_quality_score_from_risks(risks: list[RiskFlag]) -> float:
    score = 100.0
    for r in risks:
        sev = r.get("severity", "medium")
        if sev == "critical":
            score -= 25
        elif sev == "high":
            score -= 15
        elif sev == "medium":
            score -= 8
        else:
            score -= 3
    return max(0.0, min(100.0, round(score, 1)))


def compute_site_adsense_risk(post_risks: list[list[RiskFlag]]) -> str:
    """Site rollup: high / medium / low."""
    if not post_risks:
        return "low"
    total = len(post_risks)
    high_risk_posts = sum(
        1 for risks in post_risks if sum(1 for r in risks if r.get("severity") == "critical") >= 2
    )
    ratio = high_risk_posts / total
    if ratio >= 0.2:
        return "high"
    if ratio >= 0.1:
        return "medium"
    any_critical = sum(
        1 for risks in post_risks if any(r.get("severity") == "critical" for r in risks)
    )
    if any_critical / total >= 0.15:
        return "medium"
    return "low"
