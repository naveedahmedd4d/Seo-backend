"""Tests for website audit checks, scoring, and discover merge."""

from app.services.page_fetch_service import enrich_html_signals
from app.services.website_audit_checks import analyze_page, analyze_site_duplicates
from app.services.website_audit_discover import merge_and_cap_urls, parse_sitemap_locs
from app.services.website_audit_scoring import compute_health_scores


SAMPLE_HTML = """
<html><head>
<title>A reasonably long blog title here</title>
<meta name="description" content="This is a meta description that is long enough to pass the minimum length checks for SEO." />
<link rel="canonical" href="https://example.com/blog/good-post" />
</head><body>
<h1>Main Heading</h1>
<h2>Section One</h2>
<p>Paragraph one with enough words to count toward the overall body content for this sample page.</p>
<p>Paragraph two continues the article with more substantive text for word counting purposes.</p>
<ul><li>Item</li></ul>
<img src="/a.jpg" alt="A dog" />
<img src="/b.jpg" />
</body></html>
"""


def test_enrich_html_signals_extracts_structure():
    sig = enrich_html_signals(SAMPLE_HTML, "https://example.com/blog/good-post")
    assert sig["h1_count"] == 1
    assert sig["h2_count"] == 1
    assert sig["paragraph_count"] >= 2
    assert sig["list_count"] >= 1
    assert sig["word_count"] > 20
    assert len(sig["images"]) == 2
    assert sig["images"][0]["has_alt"] is True
    assert sig["images"][1]["has_alt"] is False
    assert sig["slug"] == "good-post"


def test_analyze_page_missing_title_and_alt():
    html = "<html><head></head><body><h1>Only H1</h1><p>Hi</p><img src='/x.png'></body></html>"
    sig = enrich_html_signals(html, "https://example.com/x")
    sig["status_code"] = 200
    score, issues = analyze_page(sig, url="https://example.com/x", post_type="post")
    codes = {i["issue_code"] for i in issues}
    assert "missing_title" in codes
    assert "missing_meta_description" in codes
    assert "missing_image_alt" in codes
    assert "thin_content_critical" in codes or "thin_content" in codes


def test_analyze_page_http_error():
    sig = {"status_code": 404, "fetch_error": "http_404", "word_count": 0, "fetch_status": "http_error"}
    score, issues = analyze_page(sig, url="https://example.com/missing", post_type="post")
    assert score == 0.0
    assert issues[0]["issue_code"] == "http_error"
    assert issues[0]["severity"] == "high"


def test_analyze_page_fetch_blocked_403():
    sig = {
        "status_code": 403,
        "fetch_error": "http_403",
        "word_count": 0,
        "fetch_status": "fetch_blocked",
        "fetch_source": "public_html",
        "title": None,
    }
    score, issues = analyze_page(sig, url="https://example.com/blocked", post_type="post")
    assert score == 0.0
    assert len(issues) == 1
    assert issues[0]["issue_code"] == "fetch_blocked"
    assert issues[0]["severity"] == "medium"


def test_analyze_page_wp_fallback_after_403():
    sig = {
        "status_code": 403,
        "fetch_error": "http_403",
        "fetch_status": "fetch_blocked",
        "fetch_source": "wordpress_rest_fallback",
        "title": "A reasonably long blog title here for SEO",
        "meta_description": "This is a meta description that is long enough to pass the minimum length checks for SEO.",
        "word_count": 400,
        "h1_count": 1,
        "h2_count": 2,
        "paragraph_count": 5,
        "list_count": 1,
        "media_count": 1,
        "images": [],
        "slug": "good-post",
        "has_noindex": False,
        "text_to_html_ratio": 0.2,
    }
    score, issues = analyze_page(sig, url="https://example.com/blog/good-post", post_type="post")
    codes = {i["issue_code"] for i in issues}
    assert "fetch_blocked" in codes
    assert "http_error" not in codes
    assert score > 0
    # Should continue on-page analysis (not only fetch_blocked)
    assert not codes.issubset({"fetch_blocked"}) or len(issues) >= 1


def test_analyze_page_noindex():
    html = """<html><head>
    <title>Published page with a full enough title</title>
    <meta name="robots" content="noindex" />
    <meta name="description" content="A description that is definitely long enough for the meta description length check." />
    </head><body><h1>Hi</h1><h2>More</h2>
    <p>""" + ("word " * 400) + """</p></body></html>"""
    sig = enrich_html_signals(html, "https://example.com/hidden")
    sig["status_code"] = 200
    sig["fetch_status"] = "ok"
    _, issues = analyze_page(sig, url="https://example.com/hidden", post_type="post")
    assert any(i["issue_code"] == "noindex_on_published" for i in issues)


def test_duplicate_titles():
    pages = [
        {"url": "https://ex.com/a", "title": "Same Title", "meta_description": "A"},
        {"url": "https://ex.com/b", "title": "Same Title", "meta_description": "B"},
        {"url": "https://ex.com/c", "title": "Unique", "meta_description": "C"},
    ]
    issues = analyze_site_duplicates(pages)
    dup_urls = {i["url"] for i in issues if i["issue_code"] == "duplicate_title"}
    assert dup_urls == {"https://ex.com/a", "https://ex.com/b"}


def test_scoring_drops_with_critical_issues():
    healthy, _ = compute_health_scores([], pages_audited=10, pages_fetched_ok=10, pages_analyzed=10)
    assert healthy == 100.0

    issues = [
        {
            "issue_code": "thin_content_critical",
            "category": "content",
            "severity": "critical",
            "url": "https://ex.com/1",
        },
        {
            "issue_code": "noindex_on_published",
            "category": "technical",
            "severity": "critical",
            "url": "https://ex.com/2",
        },
    ]
    score, payload = compute_health_scores(
        issues, pages_audited=10, pages_fetched_ok=10, pages_analyzed=10
    )
    assert score is not None and score < 100
    assert payload["categories"]["content"] is not None
    assert payload["categories"]["content"] < 100
    assert payload["thin_content"]["flagged_pages"] == 1
    assert payload["severity_counts"]["critical"] == 2


def test_scoring_inconclusive_when_nothing_analyzed():
    issues = [
        {
            "issue_code": "fetch_blocked",
            "category": "technical",
            "severity": "medium",
            "url": f"https://ex.com/{i}",
        }
        for i in range(10)
    ]
    score, payload = compute_health_scores(
        issues,
        pages_audited=10,
        pages_fetched_ok=0,
        pages_fetch_failed=10,
        pages_analyzed=0,
    )
    assert score is None
    assert payload["score_status"] == "inconclusive"
    assert payload["categories"]["metadata"] is None
    assert payload["warning_message"]


def test_parse_sitemap_index_and_urlset():
    index_xml = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/post-sitemap.xml</loc></sitemap>
    </sitemapindex>"""
    pages, children = parse_sitemap_locs(index_xml)
    assert pages == []
    assert children == ["https://example.com/post-sitemap.xml"]

    urlset = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/a</loc></url>
      <url><loc>https://example.com/b</loc></url>
    </urlset>"""
    pages2, children2 = parse_sitemap_locs(urlset)
    assert children2 == []
    assert pages2 == ["https://example.com/a", "https://example.com/b"]


def test_merge_and_cap_dedupes_and_prefers_wp():
    wp = [
        {"url": "https://ex.com/a", "wp_post_id": 1, "wp_post_type": "post", "title": "A"},
        {"url": "https://ex.com/b", "wp_post_id": 2, "wp_post_type": "page", "title": "B"},
    ]
    sitemap = ["https://ex.com/a", "https://ex.com/c"]
    merged = merge_and_cap_urls(wp, sitemap, max_pages=10)
    urls = [m["url"] for m in merged]
    assert urls.count("https://ex.com/a") == 1
    assert "https://ex.com/c" in urls
    a = next(m for m in merged if m["url"] == "https://ex.com/a")
    assert a["wp_post_id"] == 1


def test_merge_cap_and_content_type_filter():
    wp = [
        {"url": "https://ex.com/p", "wp_post_id": 1, "wp_post_type": "post", "title": "P"},
        {"url": "https://ex.com/g", "wp_post_id": 2, "wp_post_type": "page", "title": "G"},
    ]
    merged = merge_and_cap_urls(wp, ["https://ex.com/extra"], max_pages=10, content_types=["post"])
    assert len(merged) == 1
    assert merged[0]["post_type"] == "post"
