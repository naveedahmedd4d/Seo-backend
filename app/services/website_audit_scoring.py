"""Website Health Score for Website Audit Phase 1."""

from __future__ import annotations

from typing import Any, Optional

CATEGORY_WEIGHTS = {
    "technical": 0.25,
    "metadata": 0.20,
    "content": 0.25,
    "headings": 0.15,
    "images": 0.10,
    "url": 0.05,
}

SEVERITY_PENALTY = {
    "critical": 25.0,
    "high": 12.0,
    "medium": 6.0,
    "low": 2.0,
}

# Fetch-blocked issues should not crush the health score the same way as real 5xx.
SOFT_ISSUE_CODES = frozenset({"fetch_blocked"})

ISSUE_TO_CATEGORY = {
    "http_error": "technical",
    "fetch_blocked": "technical",
    "noindex_on_published": "technical",
    "canonical_mismatch": "technical",
    "missing_title": "metadata",
    "title_too_short": "metadata",
    "title_too_long": "metadata",
    "duplicate_title": "metadata",
    "missing_meta_description": "metadata",
    "meta_too_short": "metadata",
    "meta_too_long": "metadata",
    "duplicate_meta_description": "metadata",
    "missing_h1": "headings",
    "multiple_h1": "headings",
    "missing_h2": "headings",
    "thin_content_critical": "content",
    "thin_content": "content",
    "thin_content_shallow": "content",
    "missing_image_alt": "images",
    "slug_too_long": "url",
    "slug_has_stopwords_or_ids": "url",
}

INCONCLUSIVE_SUCCESS_RATE = 0.5


def _category_for(issue: dict[str, Any]) -> str:
    return issue.get("category") or ISSUE_TO_CATEGORY.get(issue.get("issue_code") or "", "technical")


def compute_health_scores(
    issues: list[dict[str, Any]],
    *,
    pages_audited: int,
    pages_fetched_ok: Optional[int] = None,
    pages_fetch_failed: Optional[int] = None,
    pages_from_wp_fallback: int = 0,
    pages_analyzed: Optional[int] = None,
) -> tuple[Optional[float], dict[str, Any]]:
    """
    Returns (overall_health_0_100 or None if inconclusive, category_scores payload).
    """
    n = max(pages_audited, 1)
    ok = pages_fetched_ok if pages_fetched_ok is not None else n
    failed = pages_fetch_failed if pages_fetch_failed is not None else 0
    analyzed = pages_analyzed if pages_analyzed is not None else ok
    success_rate = round(ok / n, 4) if n else 0.0

    # Pages that received real on-page analysis (public OK or WP fallback with document)
    score_denom = max(analyzed, 1)

    by_cat: dict[str, list[dict[str, Any]]] = {k: [] for k in CATEGORY_WEIGHTS}
    for issue in issues:
        cat = _category_for(issue)
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(issue)

    on_page_categories = {"metadata", "content", "headings", "images", "url"}
    had_on_page_analysis = analyzed > 0

    category_scores: dict[str, Optional[float]] = {}
    for cat in CATEGORY_WEIGHTS:
        if cat in on_page_categories and not had_on_page_analysis:
            category_scores[cat] = None
            continue
        penalty = 0.0
        for issue in by_cat.get(cat, []):
            code = issue.get("issue_code") or ""
            sev = issue.get("severity") or "medium"
            base = SEVERITY_PENALTY.get(sev, 6.0)
            if code in SOFT_ISSUE_CODES:
                base *= 0.35
            penalty += base
        normalized = penalty / score_denom
        category_scores[cat] = round(max(0.0, min(100.0, 100.0 - normalized)), 1)

    score_status = "complete"
    warning = None
    overall: Optional[float]

    if analyzed == 0:
        score_status = "inconclusive"
        overall = None
        for cat in on_page_categories:
            category_scores[cat] = None
        # Keep technical score if we only have fetch failures
        warning = (
            f"Crawl incomplete: only {ok}/{n} pages fetched successfully "
            f"({round(success_rate * 100, 1)}%). On-page category scores are unavailable."
        )
    else:
        if failed > 0 or pages_from_wp_fallback > 0 or success_rate < 1.0:
            score_status = "partial"
        if pages_from_wp_fallback > 0:
            warning = (
                f"Public crawl blocked or failed on some URLs; "
                f"{pages_from_wp_fallback} page(s) analyzed via WordPress REST fallback."
            )
        elif success_rate < INCONCLUSIVE_SUCCESS_RATE:
            score_status = "partial"
            warning = (
                f"Low public fetch success rate ({round(success_rate * 100, 1)}%). "
                "Scores reflect successfully analyzed pages only."
            )

        weighted = 0.0
        weight_sum = 0.0
        for cat, weight in CATEGORY_WEIGHTS.items():
            val = category_scores.get(cat)
            if val is None:
                continue
            weighted += weight * val
            weight_sum += weight
        overall = round(weighted / weight_sum, 1) if weight_sum else None

    thin_codes = {"thin_content_critical", "thin_content", "thin_content_shallow"}
    thin_issues = [i for i in issues if i.get("issue_code") in thin_codes]
    thin_urls = {i.get("url") for i in thin_issues}
    thin_by_sev = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for i in thin_issues:
        sev = i.get("severity") or "medium"
        if sev in thin_by_sev:
            thin_by_sev[sev] += 1

    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for i in issues:
        sev = i.get("severity") or "medium"
        if sev in severity_counts:
            severity_counts[sev] += 1

    blocked_count = sum(1 for i in issues if i.get("issue_code") == "fetch_blocked")

    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "categories": category_scores,
        "weights": CATEGORY_WEIGHTS,
        "severity_counts": severity_counts,
        "thin_content": {
            "flagged_pages": len(thin_urls),
            "flagged_percent": round(100.0 * len(thin_urls) / max(analyzed, 1), 1) if analyzed else 0.0,
            "issue_counts": thin_by_sev,
            "total_thin_issues": len(thin_issues),
        },
        "pages_audited": pages_audited,
        "pages_fetched_ok": ok,
        "pages_fetch_failed": failed,
        "pages_from_wp_fallback": pages_from_wp_fallback,
        "pages_analyzed": analyzed,
        "fetch_success_rate": success_rate,
        "score_status": score_status,
        "warning_message": warning,
        "fetch_blocked_issues": blocked_count,
        "total_issues": len(issues),
    }
    return overall, payload
