"""Multi-signal thin content analysis for Website Audit (deterministic, no LLM)."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

UTILITY_PATH_SEGMENTS = frozenset(
    {
        "contact",
        "privacy",
        "privacy-policy",
        "terms",
        "terms-of-service",
        "terms-and-conditions",
        "cart",
        "checkout",
        "login",
        "signin",
        "sign-in",
        "register",
        "signup",
        "sign-up",
        "account",
        "my-account",
        "wishlist",
        "search",
        "404",
        "cookie-policy",
        "cookies",
        "disclaimer",
        "about-us",
    }
)

TEMPLATES = {
    "thin_content_critical": {
        "title": "Critically thin content",
        "why_it_matters": (
            "Pages with almost no substantive text rarely rank and can signal low quality "
            "to search engines, especially on published blog posts."
        ),
        "recommendation": (
            "Expand this page substantially. For posts, aim for at least 300–600 words with "
            "clear H2 sections, examples, and supporting media. Consider regenerating or "
            "rewriting via Blog Generation."
        ),
    },
    "thin_content": {
        "title": "Thin content",
        "why_it_matters": (
            "Content below typical length thresholds struggles to cover search intent and "
            "is more likely to underperform against fuller competitors."
        ),
        "recommendation": (
            "Add depth: cover subtopics with H2s, include lists or examples, and bring posts "
            "to at least ~300 words (pages ~200+). Use Blog Generation if you need a full rewrite."
        ),
    },
    "thin_content_shallow": {
        "title": "Shallow content structure",
        "why_it_matters": (
            "Borderline-length pages without headings, paragraphs, or supporting media look "
            "thin to users and search engines even when word count is acceptable."
        ),
        "recommendation": (
            "Improve structure: add H2 subheadings, break text into paragraphs, and include "
            "lists, tables, or media where helpful. Target clearer topical coverage."
        ),
    },
}

SEVERITY_PRIORITY = {"critical": 90, "high": 70, "medium": 45, "low": 20}


def _path_segments(url: str) -> list[str]:
    path = (urlparse(url).path or "/").lower().strip("/")
    if not path:
        return []
    return [s for s in path.split("/") if s]


def is_utility_or_home_url(url: str) -> bool:
    segments = _path_segments(url)
    if not segments:
        return True  # homepage
    if len(segments) == 1 and segments[0] in UTILITY_PATH_SEGMENTS:
        return True
    return False


def _weak_structure(signals: dict[str, Any], *, word_floor: int) -> bool:
    h2_count = int(signals.get("h2_count") or 0)
    paragraph_count = int(signals.get("paragraph_count") or 0)
    list_count = int(signals.get("list_count") or 0)
    table_count = int(signals.get("table_count") or 0)
    media_count = int(signals.get("media_count") or 0)
    word_count = int(signals.get("word_count") or 0)

    if h2_count == 0:
        return True
    if paragraph_count < 2:
        return True
    if (
        word_count >= word_floor
        and (list_count + table_count + media_count) == 0
        and h2_count < 2
    ):
        return True
    return False


def compute_thin_score(signals: dict[str, Any], *, post_type: Optional[str] = None) -> float:
    """Return 0–100 content health score (higher is healthier)."""
    word_count = int(signals.get("word_count") or 0)
    h2_count = int(signals.get("h2_count") or 0)
    paragraph_count = int(signals.get("paragraph_count") or 0)
    ratio = float(signals.get("text_to_html_ratio") or 0.0)
    is_post = (post_type or "post").lower() == "post"

    score = 100.0
    critical = 150 if is_post else 100
    thin = 300 if is_post else 200
    shallow_hi = 500 if is_post else 350

    if word_count < critical:
        score -= 50
    elif word_count < thin:
        score -= 30
    elif word_count < shallow_hi:
        score -= 15

    if h2_count == 0:
        score -= 15
    elif h2_count < 2:
        score -= 5

    if paragraph_count < 2:
        score -= 10

    if ratio > 0 and ratio < 0.05:
        score -= 15
    elif ratio > 0 and ratio < 0.1:
        score -= 8

    return max(0.0, min(100.0, round(score, 1)))


def analyze_thin_content(
    signals: dict[str, Any],
    *,
    url: str,
    post_type: Optional[str] = None,
) -> tuple[float, list[dict[str, Any]]]:
    """
    Analyze thin content. Returns (thin_score, list of issue dicts).
    Utility/home pages only flag when word_count < 50.
    """
    word_count = int(signals.get("word_count") or 0)
    pt = (post_type or "post").lower()
    is_post = pt == "post"
    thin_score = compute_thin_score(signals, post_type=pt)

    if is_utility_or_home_url(url):
        if word_count >= 50:
            return thin_score, []
        # Extreme boilerplate emptiness still worth noting as critical
        issue = _make_issue(
            "thin_content_critical",
            "critical",
            url,
            signals,
            thin_score,
            pt,
            triggers=["utility_extremely_empty"],
        )
        return thin_score, [issue]

    critical_floor = 150 if is_post else 100
    thin_floor = 300 if is_post else 200
    shallow_low = thin_floor
    shallow_high = 500 if is_post else 350

    triggers: list[str] = []
    issue_code: Optional[str] = None
    severity: Optional[str] = None

    if word_count < critical_floor:
        issue_code = "thin_content_critical"
        severity = "critical"
        triggers.append("low_word_count")
    elif word_count < thin_floor:
        issue_code = "thin_content"
        severity = "high"
        triggers.append("low_word_count")
    elif shallow_low <= word_count < shallow_high and _weak_structure(
        signals, word_floor=thin_floor
    ):
        issue_code = "thin_content_shallow"
        severity = "medium"
        triggers.append("borderline_length")
        if int(signals.get("h2_count") or 0) == 0:
            triggers.append("no_h2")
        if int(signals.get("paragraph_count") or 0) < 2:
            triggers.append("few_paragraphs")

    # Score-based catch when hard bands did not fire but page is still weak
    if issue_code is None and thin_score < 70:
        issue_code = "thin_content_shallow"
        severity = "medium"
        triggers.append("low_thin_score")

    if not issue_code or not severity:
        return thin_score, []

    if int(signals.get("h2_count") or 0) == 0 and "no_h2" not in triggers:
        triggers.append("no_h2")

    return thin_score, [
        _make_issue(issue_code, severity, url, signals, thin_score, pt, triggers)
    ]


def _make_issue(
    issue_code: str,
    severity: str,
    url: str,
    signals: dict[str, Any],
    thin_score: float,
    post_type: str,
    triggers: list[str],
) -> dict[str, Any]:
    tmpl = TEMPLATES[issue_code]
    return {
        "url": url,
        "category": "content",
        "issue_code": issue_code,
        "title": tmpl["title"],
        "severity": severity,
        "priority_score": float(SEVERITY_PRIORITY.get(severity, 45)),
        "why_it_matters": tmpl["why_it_matters"],
        "recommendation": tmpl["recommendation"],
        "evidence": {
            "word_count": int(signals.get("word_count") or 0),
            "thin_score": thin_score,
            "post_type": post_type,
            "h2_count": int(signals.get("h2_count") or 0),
            "paragraph_count": int(signals.get("paragraph_count") or 0),
            "list_count": int(signals.get("list_count") or 0),
            "media_count": int(signals.get("media_count") or 0),
            "triggers": triggers,
        },
    }
