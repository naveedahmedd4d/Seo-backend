"""Blog generation config resolve, scaling, and template validation."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.blog_generation_config import (
    get_system_template,
    length_prompt_line,
    max_tokens_for_target,
    min_words_for_target,
    resolve_article_type,
    resolve_blog_generation_config,
    scale_section_budgets,
    validate_template_sections,
)
from app.services.openrouter_service import (
    _build_net_worth_article_prompt,
    generate_faqs,
)


def test_resolve_article_type_news_and_generic():
    assert resolve_article_type(content_intent="news_timeline") == "news_timeline"
    assert resolve_article_type(content_intent="cluster_hub") == "cluster_hub"
    assert resolve_article_type(celebrity_content_type="net_worth") == "net_worth"
    assert resolve_article_type(category="Technology & AI", topic="AI chips") == "generic"


def test_system_biography_defaults():
    cfg = resolve_blog_generation_config(article_type="biography")
    assert cfg.faq_count == 5
    assert cfg.related_query_count == 8
    assert cfg.target_word_count == 2500
    assert cfg.min_words == min_words_for_target(2500)
    assert any(s["slug"] == "intro" for s in cfg.sections)


def test_override_target_word_count_scales_min_and_tokens():
    cfg = resolve_blog_generation_config(
        article_type="news_timeline",
        overrides={"target_word_count": 3000},
    )
    assert cfg.target_word_count == 3000
    assert cfg.min_words == min_words_for_target(3000)
    assert cfg.max_tokens == max_tokens_for_target(3000)
    assert "3,000" in length_prompt_line(3000) or "3000" in length_prompt_line(3000).replace(",", "")


def test_scale_section_budgets():
    sections = [{"slug": "intro", "word_budget": [100, 200], "children": []}]
    scaled = scale_section_budgets(sections, 1000, 2000)
    assert scaled[0]["word_budget"][0] == 200
    assert scaled[0]["word_budget"][1] == 400


def test_validate_template_rejects_empty_and_cluster_without_list():
    with pytest.raises(ValueError):
        validate_template_sections([], "generic")
    system = get_system_template("generic")
    validate_template_sections(system["sections"], "generic")
    hub = get_system_template("cluster_hub")
    validate_template_sections(hub["sections"], "cluster_hub")
    stripped = [s for s in hub["sections"] if s.get("slug") != "the_list"]
    with pytest.raises(ValueError, match="cluster_members"):
        validate_template_sections(stripped, "cluster_hub")


def test_net_worth_prompt_keeps_quality_rules_with_custom_headings():
    cfg = resolve_blog_generation_config(article_type="net_worth")
    prompt = _build_net_worth_article_prompt(
        "Jane Doe",
        "",
        "jane doe net worth",
        "Celebrities",
        "",
        "> **Last Updated:** August 19, 2026",
        blog_config=cfg,
    )
    assert "Net worth estimates are based on publicly available information" in prompt
    assert "Do NOT include FAQ" in prompt or "FAQ" in prompt
    assert "ARTICLE STRUCTURE" in prompt


@pytest.mark.asyncio
async def test_generate_faqs_zero_skips_llm():
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
    ) as mock_gen:
        out = await generate_faqs("Topic", faq_count=0)
        assert out == []
        mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_generate_faqs_count_in_prompt():
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value='[{"question": "Q?", "answer": "A."}]',
    ) as mock_gen:
        await generate_faqs("Topic", faq_count=3)
        prompt = mock_gen.await_args.args[0]
        assert "Generate 3 frequently asked questions" in prompt
