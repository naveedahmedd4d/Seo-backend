"""Deterministic pre-LLM checks and baseline scores for blog content audit."""

from __future__ import annotations

import re
from typing import Any

from app.services.blog_content_audit_config import (
    NET_WORTH_BOILERPLATE_PHRASES,
    SCORE_WEIGHTS,
    SOURCE_INDICATORS,
    UNSUPPORTED_SUPERLATIVE_PATTERNS,
)
from app.services.blog_content_audit_parser import ParsedPost
from app.services.website_audit_thin_content import analyze_thin_content

Finding = dict[str, Any]


def _clamp(score: float) -> float:
    return max(0.0, min(100.0, round(score, 1)))


def _seo_score(parsed: ParsedPost) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 100.0
    title_len = len(parsed.title or "")
    if title_len < 30:
        score -= 15
        findings.append(
            {
                "severity": "medium",
                "category": "seo",
                "message": "Title is shorter than recommended (under 30 characters).",
                "evidence": f"Title length: {title_len}",
            }
        )
    elif title_len > 70:
        score -= 8
        findings.append(
            {
                "severity": "low",
                "category": "seo",
                "message": "Title may truncate in SERPs (over 70 characters).",
                "evidence": f"Title length: {title_len}",
            }
        )

    meta = (parsed.meta_description or "").strip()
    if not meta:
        score -= 20
        findings.append(
            {
                "severity": "high",
                "category": "seo",
                "message": "Missing meta description.",
                "evidence": "No meta description detected.",
            }
        )
    elif len(meta) < 70:
        score -= 10
        findings.append(
            {
                "severity": "medium",
                "category": "seo",
                "message": "Meta description is short.",
                "evidence": f"Length: {len(meta)} chars",
            }
        )

    h2_count = sum(1 for h in parsed.headings if h.get("level") == "h2")
    if h2_count < 2 and parsed.word_count > 400:
        score -= 15
        findings.append(
            {
                "severity": "medium",
                "category": "seo",
                "message": "Insufficient H2 structure for content length.",
                "evidence": f"H2 count: {h2_count}",
            }
        )

    if parsed.images_total and parsed.images_missing_alt:
        ratio = parsed.images_missing_alt / max(parsed.images_total, 1)
        penalty = min(25, ratio * 30)
        score -= penalty
        findings.append(
            {
                "severity": "medium" if ratio < 0.5 else "high",
                "category": "seo",
                "message": f"{parsed.images_missing_alt} image(s) missing alt text.",
                "evidence": f"{parsed.images_missing_alt}/{parsed.images_total} images",
            }
        )

    if parsed.internal_links < 2 and parsed.word_count > 600:
        score -= 8
        findings.append(
            {
                "severity": "low",
                "category": "seo",
                "message": "Few internal links for a long post.",
                "evidence": f"Internal links: {parsed.internal_links}",
            }
        )

    return _clamp(score), findings


def _content_quality_score(parsed: ParsedPost) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 100.0

    if parsed.duplicate_paragraph_ratio > 0.15:
        score -= 25
        findings.append(
            {
                "severity": "high",
                "category": "content_quality",
                "message": "Repeated paragraph patterns detected.",
                "evidence": f"Duplicate ratio: {parsed.duplicate_paragraph_ratio:.0%}",
            }
        )

    if parsed.paragraph_count < 4 and parsed.word_count > 500:
        score -= 12
        findings.append(
            {
                "severity": "medium",
                "category": "content_quality",
                "message": "Content lacks paragraph structure.",
                "evidence": f"Paragraphs: {parsed.paragraph_count}",
            }
        )

    thin_score, thin_issues = analyze_thin_content(
        {
            "word_count": parsed.word_count,
            "h2_count": sum(1 for h in parsed.headings if h.get("level") == "h2"),
            "h3_count": sum(1 for h in parsed.headings if h.get("level") == "h3"),
            "paragraph_count": parsed.paragraph_count,
            "images_count": parsed.images_total,
            "list_count": 0,
        },
        url=parsed.url,
        post_type="post",
    )
    if thin_issues:
        issue = thin_issues[0]
        issue_code = issue.get("issue_code", "thin_content")
        tier = "critical" if "critical" in issue_code else "thin" if issue_code == "thin_content" else "shallow"
        penalty = {"critical": 35, "thin": 22, "shallow": 12}.get(tier, 15)
        score -= penalty
        findings.append(
            {
                "severity": issue.get("severity", "medium"),
                "category": "content_quality",
                "message": f"Thin/shallow content tier: {tier}.",
                "evidence": issue.get("title", issue_code),
            }
        )
    elif thin_score < 70:
        score -= 10
        findings.append(
            {
                "severity": "low",
                "category": "content_quality",
                "message": "Borderline thin content score.",
                "evidence": f"Thin score: {thin_score}",
            }
        )

    return _clamp(score), findings


def _search_intent_score(parsed: ParsedPost) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 100.0
    title_words = set(re.findall(r"\w+", (parsed.title or "").lower()))
    h2_text = " ".join(h["text"] for h in parsed.headings if h.get("level") == "h2").lower()
    if title_words and h2_text:
        overlap = sum(1 for w in title_words if len(w) > 3 and w in h2_text)
        if overlap == 0 and len(title_words) >= 2:
            score -= 18
            findings.append(
                {
                    "severity": "medium",
                    "category": "search_intent",
                    "message": "H2 headings do not reflect title topic.",
                    "evidence": "No significant title keyword overlap in H2s.",
                }
            )

    if parsed.article_type in ("biography", "net_worth") and "faq" not in h2_text.lower():
        if not re.search(r"\?\s*$", parsed.plain_text[-800:], re.M):
            score -= 10
            findings.append(
                {
                    "severity": "low",
                    "category": "search_intent",
                    "message": "No FAQ section detected for informational intent.",
                    "evidence": "Expected FAQ or Q&A for biography/net-worth queries.",
                }
            )

    return _clamp(score), findings


def _content_depth_score(parsed: ParsedPost, missing_gaps: int) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 100.0

    if parsed.word_count < parsed.word_target:
        deficit = parsed.word_target - parsed.word_count
        ratio = deficit / max(parsed.word_target, 1)
        penalty = min(40, ratio * 50)
        score -= penalty
        findings.append(
            {
                "severity": "critical" if ratio > 0.4 else "high",
                "category": "content_depth",
                "message": f"Below word target ({parsed.word_count}/{parsed.word_target}).",
                "evidence": f"Short by {deficit} words",
            }
        )

    if missing_gaps > 0:
        score -= min(30, missing_gaps * 8)
        findings.append(
            {
                "severity": "high" if missing_gaps >= 3 else "medium",
                "category": "content_depth",
                "message": f"{missing_gaps} competitor topic(s) not covered.",
                "evidence": "From Tavily gap matrix",
            }
        )

    return _clamp(score), findings


def _originality_score(parsed: ParsedPost, site_slugs: list[str]) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 100.0
    plain_lower = parsed.plain_text.lower()

    boilerplate_hits = sum(1 for p in NET_WORTH_BOILERPLATE_PHRASES if p in plain_lower)
    if parsed.article_type == "net_worth" and boilerplate_hits >= 4:
        score -= min(30, boilerplate_hits * 5)
        findings.append(
            {
                "severity": "high",
                "category": "originality",
                "message": "Net-worth template boilerplate detected.",
                "evidence": f"{boilerplate_hits} template phrases matched",
            }
        )

    prefix = parsed.slug.rsplit("-net-worth", 1)[0] if parsed.slug else ""
    if prefix and parsed.article_type == "net_worth":
        similar = sum(
            1 for s in site_slugs if s != parsed.slug and s.startswith(prefix[:8]) and "net-worth" in s
        )
        if similar >= 2:
            score -= 15
            findings.append(
                {
                    "severity": "medium",
                    "category": "originality",
                    "message": "Possible cannibalization with similar net-worth posts.",
                    "evidence": f"{similar} similar slugs on site",
                }
            )

    return _clamp(score), findings


def _eeat_score(parsed: ParsedPost) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 100.0
    plain_lower = parsed.plain_text.lower()

    has_source = any(ind in plain_lower for ind in SOURCE_INDICATORS)
    if not has_source and parsed.word_count > 600:
        score -= 25
        findings.append(
            {
                "severity": "high",
                "category": "eeat",
                "message": "No source attribution detected.",
                "evidence": "Missing Wikipedia/sources/according-to patterns",
            }
        )

    if not parsed.has_last_updated_footer and parsed.article_type in ("biography", "net_worth"):
        score -= 12
        findings.append(
            {
                "severity": "medium",
                "category": "eeat",
                "message": "Missing Last Updated / Sources footer.",
                "evidence": "Expected for factual posts",
            }
        )

    superlative_hits = sum(1 for p in UNSUPPORTED_SUPERLATIVE_PATTERNS if p in plain_lower)
    if superlative_hits:
        score -= min(20, superlative_hits * 10)
        findings.append(
            {
                "severity": "medium",
                "category": "eeat",
                "message": "Unsupported superlative claims detected.",
                "evidence": f"{superlative_hits} pattern(s) matched",
            }
        )

    return _clamp(score), findings


def compute_deterministic_audit(
    parsed: ParsedPost,
    *,
    missing_gaps: int = 0,
    site_slugs: list[str] | None = None,
) -> dict[str, Any]:
    """Return baseline scores, findings, and word_target_met."""
    site_slugs = site_slugs or []
    all_findings: list[Finding] = []

    seo, f1 = _seo_score(parsed)
    cq, f2 = _content_quality_score(parsed)
    si, f3 = _search_intent_score(parsed)
    cd, f4 = _content_depth_score(parsed, missing_gaps)
    orig, f5 = _originality_score(parsed, site_slugs)
    eeat, f6 = _eeat_score(parsed)

    all_findings.extend(f1 + f2 + f3 + f4 + f5 + f6)

    scores = {
        "seo": seo,
        "content_quality": cq,
        "search_intent": si,
        "content_depth": cd,
        "originality": orig,
        "eeat": eeat,
        "adsense_quality": 100.0,  # filled by adsense module
    }

    overall = sum(scores[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
    word_target_met = parsed.word_count >= parsed.word_target

    return {
        "scores": scores,
        "overall_score": _clamp(overall),
        "findings": all_findings,
        "word_target_met": word_target_met,
    }
