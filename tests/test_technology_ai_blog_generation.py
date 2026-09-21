"""Technology & AI prompt quality and category-isolation regression tests."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.openrouter_service import (
    CATEGORY_PROMPTS,
    TECHNOLOGY_AI_CATEGORY,
    generate_blog_body,
    generate_single_blog_batch,
)


def test_technology_ai_editorial_brief_covers_requested_quality_rules():
    brief = CATEGORY_PROMPTS[TECHNOLOGY_AI_CATEGORY]

    assert "strongest specific data point" in brief
    assert "why the topic matters now" in brief
    assert "Never invent a source" in brief
    assert "short paragraphs" in brief
    assert "SEO, AEO, and GEO" in brief
    assert "never stuff" in brief
    assert "relevant call to action" in brief


@pytest.mark.asyncio
async def test_technology_ai_body_prompt_uses_research_and_non_stuffing_rules():
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value="Generated Technology & AI article",
    ) as mock_gen:
        result = await generate_blog_body(
            "AI inference chips",
            description="Trend signal: inference demand is rising.",
            keywords=["AI inference", "edge AI"],
            category=TECHNOLOGY_AI_CATEGORY,
            research_context="Source: Example Lab (2026) reported a 42% latency reduction.",
        )

    prompt = mock_gen.await_args.args[0]
    assert result == "Generated Technology & AI article"
    assert "Example Lab (2026)" in prompt
    assert "AI inference, edge AI" in prompt
    assert "research-supported statistic" in prompt
    assert "do not target a mechanical keyword density" in prompt
    assert "Never use merely plausible" in prompt
    assert "Aim for ~1% keyword density" not in prompt
    assert "Use only accurate, plausible data" not in prompt


@pytest.mark.asyncio
async def test_technology_ai_batch_metadata_receives_full_editorial_brief():
    metadata_json = (
        '{"title":"AI Inference Chips: Essential 2026 Guide",'
        '"meta_description":"AI inference chips explained with current evidence and practical takeaways for teams evaluating efficient AI infrastructure today.",'
        '"faqs":[],"related_queries":[]}'
    )
    with (
        patch(
            "app.services.openrouter_service.generate_blog_seo_content",
            new_callable=AsyncMock,
            side_effect=[metadata_json, "Body"],
        ) as mock_gen,
        patch(
            "app.services.openrouter_service._blog_delay",
            new_callable=AsyncMock,
        ),
    ):
        result = await generate_single_blog_batch(
            "AI inference chips",
            "A current infrastructure trend",
            ["AI inference"],
            TECHNOLOGY_AI_CATEGORY,
            "Source material from Example Lab.",
        )

    metadata_prompt = mock_gen.await_args_list[0].args[0]
    assert result["body_content"] == "Body"
    # This text is near the end of the brief, beyond the old 400-character cutoff.
    assert "easy for search engines, answer engines, and generative systems" in metadata_prompt
    assert "Source material from Example Lab" in metadata_prompt


@pytest.mark.asyncio
async def test_non_technology_body_keeps_legacy_prompt_rules():
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value="Celebrity article",
    ) as mock_gen:
        await generate_blog_body(
            "Example Person",
            category="Celebrities & Public Figures",
        )

    prompt = mock_gen.await_args.args[0]
    assert "Aim for ~1% keyword density" in prompt
    assert "Use only accurate, plausible data" in prompt
    assert "SEO, AEO, and GEO requirements" not in prompt
    assert "strongest specific data point" not in prompt

