"""News blog generation — prompt builders and generate_blog_body routing."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.openrouter_service import (
    CONTENT_INTENT_PROMPTS,
    _build_news_explainer_prompt,
    _build_news_opinion_prompt,
    _build_news_timeline_prompt,
    generate_blog_body,
)


def test_content_intent_prompts_include_news_intents():
    assert "news_timeline" in CONTENT_INTENT_PROMPTS
    assert "news_explainer" in CONTENT_INTENT_PROMPTS
    assert "news_opinion" in CONTENT_INTENT_PROMPTS
    assert "Complete Timeline" in CONTENT_INTENT_PROMPTS["news_timeline"]
    assert "Explained" in CONTENT_INTENT_PROMPTS["news_explainer"]
    assert "Editorial opinion" in CONTENT_INTENT_PROMPTS["news_opinion"]


def test_news_timeline_prompt_includes_news_sources_and_bans_biography():
    prompt = _build_news_timeline_prompt(
        "Pete Hegseth signal group chat",
        "",
        "pete hegseth signal",
        "News & Politics",
        "Research: Pete Hegseth is US Defense Secretary.",
        "> **Last Updated:** August 19, 2026",
        news_sources_block="1. Washington Post — Signal chat leak (Aug 2026)",
    )
    assert "Pete Hegseth signal group chat" in prompt
    assert "Washington Post" in prompt
    assert "NEWS article" in prompt or "news" in prompt.lower()
    assert "Do NOT write Early Life" in prompt or "Early Life" in prompt
    assert "1,800-2,500" in prompt or "1800-2500" in prompt.replace(" ", "").replace(",", "")
    assert "never embed URLs" in prompt.lower() or "never embed" in prompt.lower()


def test_news_explainer_prompt_requires_key_terms_table():
    prompt = _build_news_explainer_prompt(
        "LIV Golf contracts explained",
        "",
        "liv golf contracts",
        "Sports",
        "Research: LIV Golf is a professional golf league.",
        "> **Last Updated:** August 19, 2026",
        news_sources_block="1. ESPN — LIV Golf contract structure",
    )
    assert "LIV Golf contracts explained" in prompt
    assert "Key Terms" in prompt
    assert "GUIDE" in prompt or "explainer" in prompt.lower()
    assert "ESPN" in prompt


def test_news_opinion_prompt_includes_hook_and_editorial_label():
    prompt = _build_news_opinion_prompt(
        "Kristi Noem dog killing reaction",
        "",
        "kristi noem dog",
        "News & Politics",
        "Research: Kristi Noem is a US cabinet secretary.",
        "> **Last Updated:** August 19, 2026",
        news_sources_block="1. NYT — memoir excerpt",
        story_description="Kristi Noem described shooting her dog Cricket in her memoir.",
    )
    assert "Kristi Noem dog killing reaction" in prompt
    assert "Editorial opinion" in prompt
    assert "opening paragraph" in prompt.lower() or "hook" in prompt.lower()
    assert "Cricket" in prompt
    assert "NYT" in prompt or "nyt" in prompt.lower()


@pytest.mark.asyncio
async def test_generate_blog_body_routes_news_timeline():
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value="## What Happened\nA timeline article.",
    ) as mock_gen:
        result = await generate_blog_body(
            "Pete Hegseth signal",
            content_intent="news_timeline",
            news_sources_block="Washington Post — Signal chat",
        )
        mock_gen.assert_awaited_once()
        prompt = mock_gen.await_args.args[0]
        assert "timeline" in prompt.lower() or "What Happened" in prompt
        assert result == "## What Happened\nA timeline article."
        assert mock_gen.await_args.kwargs.get("max_tokens") == 4500


@pytest.mark.asyncio
async def test_generate_blog_body_routes_news_explainer():
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value="## Introduction\nAn explainer.",
    ) as mock_gen:
        result = await generate_blog_body(
            "WNBA salary cap explained",
            content_intent="news_explainer",
        )
        mock_gen.assert_awaited_once()
        prompt = mock_gen.await_args.args[0]
        assert "Key Terms" in prompt
        assert result.startswith("## Introduction")


@pytest.mark.asyncio
async def test_generate_blog_body_routes_news_opinion():
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value="Hook paragraph.\n## What Happened\nFacts.",
    ) as mock_gen:
        result = await generate_blog_body(
            "RFK Jr dietary controversy",
            content_intent="news_opinion",
            story_description="RFK Jr made dietary recommendations that drew criticism.",
        )
        mock_gen.assert_awaited_once()
        prompt = mock_gen.await_args.args[0]
        assert "Editorial opinion" in prompt
        assert "RFK Jr made dietary recommendations" in prompt
        assert mock_gen.await_args.kwargs.get("max_tokens") == 3500
        assert mock_gen.await_args.kwargs.get("temperature") == 0.6
        assert "Hook paragraph" in result
