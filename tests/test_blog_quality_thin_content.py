"""Blog quality: thin-content gate, fortune H1, athlete outline, hub facts, gstatic, NFC links."""

import pytest

from app.services.blog_consistency import (
    count_markdown_words,
    extract_headline_compensation,
    is_thin_body,
    rewrite_fortune_h1,
    strip_generic_filler_sections,
)
from app.services.blog_image_service import _candidate_url_fields, is_rejected_image_url
from app.services.celebrity_outline_templates import (
    get_athlete_biography_template,
    is_athlete_biography_topic,
)
from app.services.cluster_hub_links import (
    inject_cluster_hub_member_links,
    inject_sibling_article_link,
    normalize_person_key,
)
from app.services.openrouter_service import ensure_min_body_words


def test_count_markdown_words_strips_images_and_fences():
    body = (
        "Hello world from the intro.\n\n"
        "![alt](https://example.com/a.jpg)\n\n"
        "```\ncode block words here\n```\n\n"
        "More [linked](https://x.com) text here."
    )
    # Images + fences stripped; link text kept → "Hello world from the intro. More linked text here."
    assert count_markdown_words(body) == 9
    assert is_thin_body(body, 100) is True
    assert is_thin_body("word " * 200, 100) is False


def test_rewrite_fortune_h1():
    assert rewrite_fortune_h1(
        "How She Built Her $5M Fortune", name="Caitlin Clark", year=2026
    ) == "Caitlin Clark Net Worth 2026: Salary, Endorsements & Earnings"
    assert rewrite_fortune_h1(
        "Angel Reese Net Worth 2026: How She Built Her $8M Fortune",
        name="Angel Reese",
    ) == "Angel Reese Net Worth 2026: Salary, Endorsements & Earnings"
    assert rewrite_fortune_h1("Angel Reese Net Worth 2026: Salary & Earnings") == (
        "Angel Reese Net Worth 2026: Salary & Earnings"
    )


def test_strip_generic_filler_sections():
    body = (
        "## Career Earnings\n\n"
        + ("Salary details and contract context. " * 20)
        + "\n\n"
        "## Investments, Real Estate\n\n"
        "- Specific details are not publicly available\n"
        "- Relatively private personal life\n\n"
        "## Conclusion\n\n"
        "Wrap up."
    )
    cleaned = strip_generic_filler_sections(body)
    assert "## Investments" not in cleaned
    assert "## Career Earnings" in cleaned
    assert "## Conclusion" in cleaned


def test_athlete_outline_no_first_leading_role():
    headings = [s.heading for s in get_athlete_biography_template()]
    assert "Draft & Pro Career" in headings
    assert all("First Leading Role" not in h for h in headings)
    assert is_athlete_biography_topic("Caitlin Clark", "Sports", None) is True
    assert is_athlete_biography_topic(
        "Someone",
        "Celebrities & Public Figures",
        "Parent cluster category: Sports. Prefer athlete",
    ) is True
    assert is_athlete_biography_topic(
        "Actor Name", "Celebrities & Public Figures", "film premiere"
    ) is False
    assert is_athlete_biography_topic(
        "Player", "Celebrities & Public Figures", "WNBA guard salary"
    ) is True


def test_extract_headline_compensation_prefers_salary():
    body = (
        "Intro about the star.\n\n"
        "## Quick Facts\n\n"
        "| Field | Value |\n| --- | --- |\n"
        "| 2026 salary | $200,000 |\n"
        "| Net worth | $5 million |\n\n"
        "## Career\n\nMore."
    )
    assert extract_headline_compensation(body) == "$200,000"


def test_hub_facts_blank_when_missing():
    """Pre-filled table leaves Estimated blank when no fact — prompt builder unit."""
    from app.services.openrouter_service import _build_cluster_hub_article_prompt

    prompt = _build_cluster_hub_article_prompt(
        "WNBA Net Worth 2026",
        "",
        "wnba",
        "style",
        "",
        "> **Last Updated:** January 1, 2026 | **Sources:** Wikipedia",
        cluster_subs=["Caitlin Clark", "A'ja Wilson"],
        cluster_member_facts=[
            {
                "name": "Caitlin Clark",
                "slug": "/caitlin-clark-net-worth",
                "headline_compensation": "$76,535",
            }
        ],
    )
    assert "| Caitlin Clark |" in prompt
    assert "$76,535" in prompt
    assert "| A'ja Wilson |  |  |" in prompt or "| A'ja Wilson |" in prompt
    assert "Do not invent fortunes for blank cells" in prompt


def test_gstatic_rejected():
    assert is_rejected_image_url(
        "https://encrypted-tbn0.gstatic.com/images?q=tbn:foo"
    )
    assert is_rejected_image_url("https://www.gstatic.com/images/branding/foo.png")
    cand = {
        "original": "https://cdn.example.com/real.jpg",
        "thumbnail": "https://encrypted-tbn0.gstatic.com/images?q=tbn:x",
        "link": "https://example.com/page",
    }
    urls = _candidate_url_fields(cand)
    assert urls[0] == "https://cdn.example.com/real.jpg"
    assert all("gstatic" not in u for u in urls)
    only_thumb = {"thumbnail": "https://encrypted-tbn0.gstatic.com/images?q=tbn:x"}
    assert _candidate_url_fields(only_thumb) == []


def test_nfc_apostrophe_link_matching():
    assert normalize_person_key("A'ja Wilson") == normalize_person_key("A\u2019ja Wilson")
    body = "### A\u2019ja Wilson\n\nForward for Las Vegas.\n\n### Flau'jae Johnson\n\nGuard.\n"
    result = inject_cluster_hub_member_links(
        body,
        [
            {"name": "A'ja Wilson", "path": "/aja-wilson-net-worth"},
            {"name": "Flau'jae Johnson", "path": "/flaujae-johnson-net-worth"},
        ],
    )
    assert "/aja-wilson-net-worth" in result
    assert "/flaujae-johnson-net-worth" in result
    # First member still linked (not skipped by bottom-up insert)
    assert result.index("### A") < result.index("/aja-wilson-net-worth")


def test_sibling_article_link():
    body = "Intro paragraph about the player.\n\n## Quick Facts\n\nTable."
    out = inject_sibling_article_link(
        body, label="Net worth profile", path="/caitlin-clark-net-worth"
    )
    assert "[Net worth profile](/caitlin-clark-net-worth)" in out
    again = inject_sibling_article_link(
        out, label="Net worth profile", path="/caitlin-clark-net-worth"
    )
    assert again.count("/caitlin-clark-net-worth") == 1


@pytest.mark.asyncio
async def test_ensure_min_body_words_expands_once(monkeypatch):
    short = "word " * 50
    long = "expanded " * 1300

    async def fake_expand(*_args, **_kwargs):
        return long.strip()

    monkeypatch.setattr(
        "app.services.openrouter_service.expand_thin_blog_body",
        fake_expand,
    )
    body, still_thin = await ensure_min_body_words(
        "Caitlin Clark",
        short,
        min_words=1150,
        content_intent="net_worth",
    )
    assert still_thin is False
    assert count_markdown_words(body) >= 1150


@pytest.mark.asyncio
async def test_ensure_min_body_words_flags_if_still_thin(monkeypatch):
    short = "word " * 40
    slightly_longer = "word " * 80

    async def fake_expand(*_args, **_kwargs):
        return slightly_longer

    monkeypatch.setattr(
        "app.services.openrouter_service.expand_thin_blog_body",
        fake_expand,
    )
    body, still_thin = await ensure_min_body_words(
        "Topic",
        short,
        min_words=1150,
        content_intent="net_worth",
    )
    assert still_thin is True
    assert count_markdown_words(body) == count_markdown_words(slightly_longer)
