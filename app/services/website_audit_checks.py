"""Deterministic Website Audit on-page checks with template recommendations."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional
from urllib.parse import urlparse

from app.services.page_fetch_service import classify_fetch_status, is_blocked_status
from app.services.website_audit_thin_content import (
    SEVERITY_PRIORITY,
    analyze_thin_content,
)

TITLE_MIN = 30
TITLE_MAX = 60
META_MIN = 70
META_MAX = 160
SLUG_MAX_LEN = 75
SLUG_STOPWORDS = frozenset(
    {"a", "an", "the", "and", "or", "but", "of", "for", "to", "in", "on", "at", "by"}
)

TEMPLATES: dict[str, dict[str, str]] = {
    "http_error": {
        "title": "HTTP error response",
        "why_it_matters": "Broken pages cannot be indexed or convert visitors and waste crawl budget.",
        "recommendation": "Fix the server/application error or restore/redirect the URL to a live page.",
    },
    "fetch_blocked": {
        "title": "Public crawl blocked",
        "why_it_matters": (
            "The audit crawler could not retrieve the public HTML (often WAF/bot protection). "
            "This does not always mean visitors or Googlebot cannot access the page."
        ),
        "recommendation": (
            "Allowlist the TrendifyPressAudit user-agent in Cloudflare/WAF, or rely on "
            "WordPress-connected analysis (REST fallback). Re-run the audit after allowlisting."
        ),
    },
    "missing_title": {
        "title": "Missing meta title",
        "why_it_matters": "Titles are a primary ranking and CTR signal in search results.",
        "recommendation": "Add a unique, descriptive title of roughly 30–60 characters including the primary topic.",
    },
    "title_too_short": {
        "title": "Meta title too short",
        "why_it_matters": "Very short titles rarely describe the page well and may underperform in SERPs.",
        "recommendation": "Expand the title to about 30–60 characters with clear topic keywords.",
    },
    "title_too_long": {
        "title": "Meta title too long",
        "why_it_matters": "Long titles get truncated in search results, hiding important keywords.",
        "recommendation": "Shorten the title to roughly 60 characters while keeping the main intent.",
    },
    "duplicate_title": {
        "title": "Duplicate meta title",
        "why_it_matters": "Duplicate titles confuse search engines and dilute CTR across similar pages.",
        "recommendation": "Make each page title unique and specific to that URL’s topic.",
    },
    "missing_meta_description": {
        "title": "Missing meta description",
        "why_it_matters": "Descriptions influence click-through even though they are not a direct ranking factor.",
        "recommendation": "Write a unique 70–160 character description summarizing the page value.",
    },
    "meta_too_short": {
        "title": "Meta description too short",
        "why_it_matters": "Short descriptions waste SERP real estate and rarely persuade clicks.",
        "recommendation": "Expand the description toward 120–160 characters with a clear benefit.",
    },
    "meta_too_long": {
        "title": "Meta description too long",
        "why_it_matters": "Overlong descriptions are truncated in search results.",
        "recommendation": "Trim to about 160 characters, leading with the strongest message.",
    },
    "duplicate_meta_description": {
        "title": "Duplicate meta description",
        "why_it_matters": "Repeated descriptions make pages look interchangeable in search.",
        "recommendation": "Write a unique description for each URL reflecting its specific content.",
    },
    "missing_h1": {
        "title": "Missing H1 heading",
        "why_it_matters": "An H1 clarifies the primary topic for users and search engines.",
        "recommendation": "Add a single descriptive H1 that matches the page’s main topic.",
    },
    "multiple_h1": {
        "title": "Multiple H1 headings",
        "why_it_matters": "Multiple H1s dilute topical focus and can confuse page hierarchy.",
        "recommendation": "Keep one H1; demote extra headings to H2/H3.",
    },
    "missing_h2": {
        "title": "Missing H2 headings",
        "why_it_matters": "H2s structure content for scannability and help cover subtopics.",
        "recommendation": "Add H2 sections that break the topic into clear subtopics.",
    },
    "missing_image_alt": {
        "title": "Images missing alt text",
        "why_it_matters": "Alt text improves accessibility and helps image search relevance.",
        "recommendation": "Add concise, descriptive alt text to informative images (skip pure decoration).",
    },
    "slug_too_long": {
        "title": "URL slug too long",
        "why_it_matters": "Long slugs are harder to share, remember, and may look spammy.",
        "recommendation": "Shorten the slug to a concise keyword phrase under ~75 characters.",
    },
    "slug_has_stopwords_or_ids": {
        "title": "URL slug needs cleanup",
        "why_it_matters": "Numeric IDs and filler words make URLs less readable and less keyword-focused.",
        "recommendation": "Use a clean hyphenated slug focused on the main topic without IDs or stop words.",
    },
    "noindex_on_published": {
        "title": "Published page is noindex",
        "why_it_matters": "noindex prevents the page from appearing in Google search results.",
        "recommendation": "Remove noindex from robots meta (or headers) if this URL should be indexed.",
    },
    "canonical_mismatch": {
        "title": "Canonical URL mismatch",
        "why_it_matters": "A mismatched canonical can send ranking signals to a different URL than intended.",
        "recommendation": "Point the canonical to the preferred version of this page (usually itself).",
    },
}


def _issue(
    issue_code: str,
    severity: str,
    url: str,
    *,
    category: str,
    evidence: Optional[dict[str, Any]] = None,
    priority_boost: float = 0.0,
) -> dict[str, Any]:
    tmpl = TEMPLATES[issue_code]
    return {
        "url": url,
        "category": category,
        "issue_code": issue_code,
        "title": tmpl["title"],
        "severity": severity,
        "priority_score": float(SEVERITY_PRIORITY.get(severity, 45)) + priority_boost,
        "why_it_matters": tmpl["why_it_matters"],
        "recommendation": tmpl["recommendation"],
        "evidence": evidence or {},
    }


def _normalize_host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().replace("www.", "")


def _slug_issues(url: str, slug: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not slug:
        return issues
    if len(slug) > SLUG_MAX_LEN:
        issues.append(
            _issue(
                "slug_too_long",
                "medium",
                url,
                category="url",
                evidence={"slug": slug, "length": len(slug)},
            )
        )
    tokens = [t for t in slug.lower().replace("_", "-").split("-") if t]
    has_stop = any(t in SLUG_STOPWORDS for t in tokens)
    has_id = any(t.isdigit() and len(t) >= 3 for t in tokens) or any(
        t.endswith(".html") for t in tokens
    )
    if has_stop or has_id:
        issues.append(
            _issue(
                "slug_has_stopwords_or_ids",
                "low",
                url,
                category="url",
                evidence={"slug": slug, "tokens": tokens, "has_stopwords": has_stop, "has_id": has_id},
            )
        )
    return issues


def analyze_page(
    signals: dict[str, Any],
    *,
    url: str,
    post_type: Optional[str] = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Run all per-page checks. Returns (thin_score, issues)."""
    issues: list[dict[str, Any]] = []
    status = signals.get("status_code")
    fetch_error = signals.get("fetch_error")
    fetch_status = signals.get("fetch_status") or classify_fetch_status(
        status_code=status, fetch_error=fetch_error
    )
    fetch_source = signals.get("fetch_source") or "public_html"
    has_document = int(signals.get("word_count") or 0) > 0 or bool(
        (signals.get("title") or "").strip()
    )

    # Public fetch blocked/failed but we have WP REST (or other) document signals — analyze those.
    if has_document and fetch_source in ("wordpress_rest", "wordpress_rest_fallback", "cache"):
        if fetch_status == "fetch_blocked" or (
            status is not None and is_blocked_status(int(status))
        ):
            issues.append(
                _issue(
                    "fetch_blocked",
                    "medium",
                    url,
                    category="technical",
                    evidence={
                        "status_code": status,
                        "fetch_error": fetch_error,
                        "public_fetch_blocked": True,
                        "analyzed_via": fetch_source,
                    },
                )
            )
        # Continue into on-page checks below
    elif fetch_status == "fetch_blocked" or (
        status is not None and is_blocked_status(int(status)) and not has_document
    ):
        issues.append(
            _issue(
                "fetch_blocked",
                "medium",
                url,
                category="technical",
                evidence={"status_code": status, "fetch_error": fetch_error},
            )
        )
        return 0.0, issues
    elif status is not None and int(status) >= 400 and not has_document:
        sev = "critical" if int(status) >= 500 else "high"
        if int(status) in (404, 410):
            sev = "high"
        issues.append(
            _issue(
                "http_error",
                sev,
                url,
                category="technical",
                evidence={"status_code": status, "fetch_error": fetch_error},
            )
        )
        return 0.0, issues
    elif fetch_error and status is None and not has_document:
        code = "fetch_blocked" if fetch_status == "timeout" else "http_error"
        sev = "medium" if code == "fetch_blocked" else "high"
        issues.append(
            _issue(
                code if code in TEMPLATES else "http_error",
                sev,
                url,
                category="technical",
                evidence={"fetch_error": fetch_error, "fetch_status": fetch_status},
            )
        )
        # Map timeout to fetch_blocked template
        if fetch_status == "timeout":
            issues[-1] = _issue(
                "fetch_blocked",
                "medium",
                url,
                category="technical",
                evidence={"fetch_error": fetch_error, "fetch_status": "timeout"},
            )
        return 0.0, issues

    title = (signals.get("title") or "").strip()
    meta = (signals.get("meta_description") or "").strip()
    h1_count = int(signals.get("h1_count") or 0)
    h2_count = int(signals.get("h2_count") or 0)
    word_count = int(signals.get("word_count") or 0)

    if not title:
        issues.append(_issue("missing_title", "high", url, category="metadata", evidence={"title": title}))
    elif len(title) < TITLE_MIN:
        issues.append(
            _issue(
                "title_too_short",
                "medium",
                url,
                category="metadata",
                evidence={"title": title, "length": len(title)},
            )
        )
    elif len(title) > TITLE_MAX:
        issues.append(
            _issue(
                "title_too_long",
                "medium",
                url,
                category="metadata",
                evidence={"title": title, "length": len(title)},
            )
        )

    if not meta:
        issues.append(
            _issue(
                "missing_meta_description",
                "high",
                url,
                category="metadata",
                evidence={},
            )
        )
    elif len(meta) < META_MIN:
        issues.append(
            _issue(
                "meta_too_short",
                "medium",
                url,
                category="metadata",
                evidence={"length": len(meta)},
            )
        )
    elif len(meta) > META_MAX:
        issues.append(
            _issue(
                "meta_too_long",
                "low",
                url,
                category="metadata",
                evidence={"length": len(meta)},
            )
        )

    if h1_count == 0:
        issues.append(_issue("missing_h1", "high", url, category="headings", evidence={"h1_count": 0}))
    elif h1_count > 1:
        issues.append(
            _issue(
                "multiple_h1",
                "medium",
                url,
                category="headings",
                evidence={"h1_count": h1_count, "h1": signals.get("h1")},
            )
        )

    if word_count >= 200 and h2_count == 0:
        issues.append(
            _issue(
                "missing_h2",
                "medium",
                url,
                category="headings",
                evidence={"h2_count": 0, "word_count": word_count},
            )
        )

    images = signals.get("images") or []
    missing_alt = [img for img in images if not img.get("has_alt")]
    if missing_alt:
        sev = "medium" if len(missing_alt) >= 3 else "low"
        issues.append(
            _issue(
                "missing_image_alt",
                sev,
                url,
                category="images",
                evidence={"missing_count": len(missing_alt), "total_images": len(images)},
            )
        )

    issues.extend(_slug_issues(url, signals.get("slug") or ""))

    if signals.get("has_noindex"):
        issues.append(
            _issue(
                "noindex_on_published",
                "critical",
                url,
                category="technical",
                evidence={"has_noindex": True},
            )
        )

    canonical = (signals.get("canonical") or "").strip()
    final_url = (signals.get("final_url") or url).strip()
    if canonical:
        same_host = _normalize_host(canonical) == _normalize_host(final_url)
        canon_path = (urlparse(canonical).path or "/").rstrip("/") or "/"
        final_path = (urlparse(final_url).path or "/").rstrip("/") or "/"
        if not same_host or canon_path != final_path:
            issues.append(
                _issue(
                    "canonical_mismatch",
                    "medium",
                    url,
                    category="technical",
                    evidence={"canonical": canonical, "final_url": final_url},
                )
            )

    thin_score, thin_issues = analyze_thin_content(signals, url=url, post_type=post_type)
    issues.extend(thin_issues)
    return thin_score, issues


def find_duplicate_field_issues(
    pages: list[dict[str, Any]],
    *,
    field: str,
    issue_code: str,
) -> list[dict[str, Any]]:
    """Site-level duplicate title/meta detection. pages: {url, title?, meta_description?}."""
    groups: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        value = (page.get(field) or "").strip().lower()
        if not value:
            continue
        groups[value].append(page["url"])

    issues: list[dict[str, Any]] = []
    for value, urls in groups.items():
        if len(urls) < 2:
            continue
        for u in urls:
            issues.append(
                _issue(
                    issue_code,
                    "high",
                    u,
                    category="metadata",
                    evidence={"value": value, "duplicate_urls": urls},
                )
            )
    return issues


def analyze_site_duplicates(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    issues.extend(
        find_duplicate_field_issues(pages, field="title", issue_code="duplicate_title")
    )
    issues.extend(
        find_duplicate_field_issues(
            pages, field="meta_description", issue_code="duplicate_meta_description"
        )
    )
    return issues
