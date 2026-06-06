"""Tests for news research service."""

import pytest
from unittest.mock import AsyncMock, patch

from app.services.news_research_service import (
    NewsResearchBundle,
    build_news_bundle,
    build_fallback_biography_meta_description,
    clamp_biography_meta_description,
    ensure_biography_seo_title,
    format_news_bundle_for_llm,
    get_biography_title_hook_variants,
    has_biography_hook_tail,
    is_weak_biography_meta,
    is_weak_biography_title,
    normalize_news_headline,
    pick_biography_title_hook,
    estimate_engagement_score,
)


def test_normalize_news_headline_single_line():
    assert normalize_news_headline("Taylor Swift Breaks Record") == "Taylor Swift Breaks Record"


def test_normalize_news_headline_two_lines():
    headline = "SpaceX Starship megarocket\nFinds mixed success in debut test flight"
    result = normalize_news_headline(headline)
    assert "—" in result
    assert "SpaceX" in result
    assert "mixed success" in result


def test_normalize_news_headline_line1_ends_with_em_dash():
    headline = "Event happens —\nFans react strongly"
    result = normalize_news_headline(headline)
    assert result == "Event happens — Fans react strongly"


def test_normalize_news_headline_strips_pro1_highlight_markup():
    headline = "**Tools that** reveal\nyour hidden data"
    result = normalize_news_headline(headline)
    assert "**" not in result
    assert "Tools that" in result
    assert "reveal" in result


def test_format_news_bundle_for_llm():
    bundle = NewsResearchBundle(
        query="nijaree canady",
        headline="Line1\nLine2",
        story_description="She signed a major deal.",
        description="Short meta text.",
        hashtags=["#Softball", "#NIL"],
        news_results=[
            {
                "title": "Golden ticket news",
                "link": "https://example.com/a",
                "source": "ESPN",
                "snippet": "First recipient of golden ticket.",
                "date": "2 days ago",
            }
        ],
    )
    text = format_news_bundle_for_llm(bundle)
    assert "nijaree canady" in text
    assert "She signed a major deal." in text
    assert "Golden ticket news" in text
    assert "https://example.com/a" in text
    assert "#Softball" in text


def test_is_weak_biography_title_social_style():
    assert is_weak_biography_title(
        "Jorge Mateo hits 4th homer — Fans praise his power surge",
        "Jorge Mateo",
    )


def test_is_weak_biography_title_strong():
    assert not is_weak_biography_title(
        "Jorge Mateo Hits 4th Homer for Atlanta Braves — Who Is He Really?",
        "Jorge Mateo",
    )


def test_pick_biography_title_hook_varies_by_name():
    hooks = {pick_biography_title_hook(n, 2026) for n in ("Alice", "Bob", "Carrie", "Diana", "Elon")}
    assert len(hooks) >= 2
    assert len(get_biography_title_hook_variants(2026)) >= 8


def test_ensure_biography_seo_title_fixes_weak():
    fixed = ensure_biography_seo_title(
        "Jorge Mateo hits 4th homer — Fans praise his power surge",
        "Jorge Mateo",
        year=2026,
    )
    assert has_biography_hook_tail(fixed)
    assert "fans praise" not in fixed.lower()
    assert "Jorge Mateo" in fixed


def test_ensure_biography_seo_title_uses_person_specific_hook():
    a = ensure_biography_seo_title("", "Taylor Swift", year=2026)
    b = ensure_biography_seo_title("", "Elon Musk", year=2026)
    assert has_biography_hook_tail(a)
    assert has_biography_hook_tail(b)
    assert a.split("—")[-1].strip() != b.split("—")[-1].strip() or "Taylor" != "Elon"


def test_is_weak_biography_meta_short_social_style():
    assert is_weak_biography_meta(
        "Jorge Mateo hit his 4th home run, a solo shot. He's gaining praise for his power surge.",
        "Jorge Mateo hits 4th homer — Fans praise his power surge",
        year=2026,
    )


def test_clamp_biography_meta_description_length():
    meta = build_fallback_biography_meta_description(
        "Jorge Mateo",
        "Jorge Mateo Hits 4th Homer for Atlanta Braves — Who Is He Really?",
        year=2026,
    )
    assert 150 <= len(meta) <= 160
    assert "2026" in meta
    assert "biography" in meta.lower()
    assert "net worth" in meta.lower()
    assert "religion" in meta.lower()
    assert any(w in meta.lower() for w in ("learn", "discover", "explore", "find out"))


def test_estimate_engagement_score_high():
    assert estimate_engagement_score("Breaking news shocks fans") == "High"


@pytest.mark.asyncio
async def test_build_news_bundle_success():
    mock_results = [{"title": "T", "link": "https://x.com", "source": "S", "snippet": "Sn", "date": "today"}]
    with patch(
        "app.services.news_research_service.fetch_google_news_light",
        new_callable=AsyncMock,
        return_value=mock_results,
    ), patch(
        "app.services.news_research_service.generate_news_headline",
        new_callable=AsyncMock,
        return_value="Head\nLine",
    ), patch(
        "app.services.news_research_service.generate_social_post_description",
        new_callable=AsyncMock,
        return_value="Short desc.",
    ), patch(
        "app.services.news_research_service.generate_social_post_story_description",
        new_callable=AsyncMock,
        return_value="Story hook paragraph.",
    ), patch(
        "app.services.news_research_service.generate_social_post_hashtags",
        new_callable=AsyncMock,
        return_value=["#News"],
    ):
        bundle = await build_news_bundle("test query")
    assert bundle is not None
    assert bundle.query == "test query"
    assert bundle.story_description == "Story hook paragraph."
    assert len(bundle.news_results) == 1


@pytest.mark.asyncio
async def test_build_news_bundle_empty_results():
    with patch(
        "app.services.news_research_service.fetch_google_news_light",
        new_callable=AsyncMock,
        return_value=[],
    ):
        bundle = await build_news_bundle("empty")
    assert bundle is None


@pytest.mark.asyncio
async def test_build_news_bundle_failure_returns_none():
    with patch(
        "app.services.news_research_service.fetch_google_news_light",
        new_callable=AsyncMock,
        side_effect=Exception("api down"),
    ):
        bundle = await build_news_bundle("fail")
    assert bundle is None
