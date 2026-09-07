"""Tests for research summary generation."""

import json
import pytest
from unittest.mock import AsyncMock, patch

from app.services.openrouter_service import (
    generate_research_content_summary,
    _fallback_research_summary,
    _parse_heading_nodes,
)
from app.schemas.research import ResearchSummaryRequest, ResearchSummaryResponse, HeadingNode


SAMPLE_RESULTS = {
    "answer": "Jason Momoa is an actor known for Aquaman and Game of Thrones.",
    "follow_up_questions": ["What is Jason Momoa's net worth?", "How tall is Jason Momoa?"],
    "results": [
        {"title": "Wikipedia", "content": "Biography and filmography.", "score": 0.9},
    ],
}

VALID_LLM_JSON = {
    "summary": "Executive summary about the topic.",
    "h1": "Complete Guide to Jason Momoa",
    "outline": [
        {
            "level": 2,
            "text": "Early Life",
            "children": [{"level": 3, "text": "Background", "children": []}],
        },
        {"level": 2, "text": "Career Highlights", "children": []},
    ],
    "key_topics": ["Acting", "Aquaman"],
    "subtopics": ["HBO", "DC Films"],
    "core_content_recommendations": ["Cover filmography", "Include recent projects"],
    "seo_suggestions": ["Use focus keyword in title"],
    "topical_coverage": ["Biography", "Movies"],
    "target_keywords": ["jason momoa", "aquaman actor"],
}


def test_parse_heading_nodes():
    nodes = _parse_heading_nodes(VALID_LLM_JSON["outline"])
    assert len(nodes) == 2
    assert nodes[0]["level"] == 2
    assert len(nodes[0]["children"]) == 1


def test_fallback_research_summary():
    data = _fallback_research_summary("jason momoa", SAMPLE_RESULTS)
    assert "jason momoa" in data["h1"].lower() or data["h1"] == "jason momoa"
    assert data["summary"]
    assert len(data["key_topics"]) >= 1


@pytest.mark.asyncio
async def test_generate_research_content_summary_valid_json():
    mock_clusters = {
        "primary": "jason momoa",
        "secondary": ["aquaman"],
        "lsi": ["actor"],
        "paa": ["How old is Jason Momoa?"],
        "long_tail": ["jason momoa movies list"],
    }
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value=json.dumps(VALID_LLM_JSON),
    ), patch(
        "app.services.openrouter_service.extract_keyword_clusters",
        new_callable=AsyncMock,
        return_value=mock_clusters,
    ):
        result = await generate_research_content_summary(
            query="jason momoa",
            results=SAMPLE_RESULTS,
            competitors=[{"domain": "wikipedia.org"}],
            opportunities=[{"keyword": "jason momoa net worth"}],
        )
    assert result["h1"] == "Complete Guide to Jason Momoa"
    assert len(result["outline"]) == 2
    assert "jason momoa" in result["target_keywords"][0].lower()
    assert any("PAA" in s or "long-tail" in s.lower() for s in result["seo_suggestions"])


@pytest.mark.asyncio
async def test_generate_research_content_summary_uses_topical_mapping():
    mock_clusters = {
        "primary": "jutta leerdam",
        "secondary": [],
        "lsi": [],
        "paa": [],
        "long_tail": [],
    }
    topical_mapping = {
        "query": "jutta leerdam",
        "autocomplete_suggestions": [{"value": "jutta leerdam height"}],
        "related_queries": [{"query": "jutta leerdam olympics schedule"}],
    }
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value=json.dumps(VALID_LLM_JSON),
    ), patch(
        "app.services.openrouter_service.extract_keyword_clusters",
        new_callable=AsyncMock,
        return_value=mock_clusters,
    ):
        result = await generate_research_content_summary(
            query="jutta leerdam",
            results=SAMPLE_RESULTS,
            topical_mapping=topical_mapping,
        )
    assert "jutta leerdam height" in result["target_keywords"]
    assert "jutta leerdam olympics schedule" in result["topical_coverage"]


def test_extract_topical_mapping_terms():
    from app.services.research_context import (
        extract_topical_mapping_terms,
        format_topical_mapping_block,
        merge_topical_mapping_terms,
    )

    payload = {
        "query": "jutta leerdam",
        "autocomplete_suggestions": [{"value": "jutta leerdam bikini"}],
        "related_queries": [{"query": "jutta leerdam pants"}],
    }
    terms = extract_topical_mapping_terms(payload)
    assert terms == ["jutta leerdam bikini", "jutta leerdam pants"]
    block = format_topical_mapping_block(payload)
    assert "Autocomplete: jutta leerdam bikini" in block
    assert "Related queries: jutta leerdam pants" in block
    merged = merge_topical_mapping_terms(["existing"], payload)
    assert merged == ["existing", "jutta leerdam bikini", "jutta leerdam pants"]


@pytest.mark.asyncio
async def test_generate_research_content_summary_invalid_json_fallback():
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value="not valid json {{{",
    ), patch(
        "app.services.openrouter_service.extract_keyword_clusters",
        new_callable=AsyncMock,
        return_value={"primary": "x", "secondary": [], "lsi": [], "paa": [], "long_tail": []},
    ):
        result = await generate_research_content_summary(
            query="jason momoa",
            results=SAMPLE_RESULTS,
        )
    assert result["h1"] == "jason momoa"
    assert result["summary"]


def test_research_summary_request_validation():
    req = ResearchSummaryRequest(query="test", results=SAMPLE_RESULTS)
    assert req.query == "test"


def test_research_summary_response_schema():
    resp = ResearchSummaryResponse(
        summary="Summary text",
        h1="Title",
        outline=[HeadingNode(level=2, text="Section", children=[])],
        key_topics=["A"],
        subtopics=["B"],
        core_content_recommendations=["C"],
        seo_suggestions=["D"],
        topical_coverage=["E"],
        target_keywords=["kw"],
    )
    assert resp.h1 == "Title"


def test_normalize_celebrity_name():
    from app.services.celebrity_outline_templates import normalize_celebrity_name

    assert normalize_celebrity_name("what religion is emilia clarke") == "Emilia Clarke"
    assert normalize_celebrity_name("emilia clarke") == "Emilia Clarke"
    assert normalize_celebrity_name("jutta leerdam net worth") == "Jutta Leerdam"


def test_build_celebrity_religion_outline():
    from app.services.celebrity_outline_templates import build_celebrity_religion_outline

    outline = build_celebrity_religion_outline(celebrity_name="Emilia Clarke")
    h2_texts = [n["text"] for n in outline if n["level"] == 2]
    assert len(h2_texts) == 12
    assert any("Emilia Clarke" in t for t in h2_texts)
    assert any("Faith in the Limelight" in t for t in h2_texts)
    assert any("5 Key Facts" in t for t in h2_texts)
    spotlight = next(n for n in outline if "Faith Behind the Fame" in n["text"])
    assert "Emilia Clarke" in spotlight["text"]
    assert len(spotlight.get("children") or []) >= 1


def test_religion_format_rules_mention_word_count():
    from app.services.celebrity_outline_templates import get_religion_format_rules

    rules = get_religion_format_rules()
    assert "1,500" in rules
    assert "3" in rules and "bullet" in rules.lower()


def test_religion_quality_rules_direct_answer_and_five_facts():
    from app.services.celebrity_outline_templates import get_religion_quality_rules

    rules = get_religion_quality_rules("Ana de Armas")
    assert "5 Key Facts" in rules
    assert "Ana de Armas" in rules
    assert "example.com" in rules
    assert "UNIQUE" in rules


def test_sanitize_religion_body():
    from app.services.openrouter_service import sanitize_religion_body

    body = "Intro\n\n**Table of Contents**\n\n- [Intro](#intro)\n\n## Section\n\n![x](https://example.com/x.jpg)\n"
    cleaned = sanitize_religion_body(body)
    assert "Table of Contents" not in cleaned
    assert "example.com" not in cleaned


@pytest.mark.asyncio
async def test_celebrity_religious_summary_uses_template_outline():
    religious_json = {
        "summary": "Emilia Clarke was raised in England. Her faith is often discussed in interviews.",
        "h1": "What Religion Is Emilia Clarke? Faith Behind the Fame",
        "section_notes": {
            "spotlight": ["Quote from 2023 interview", "Note private faith", "Link HBO role"],
            "intro": ["Quick facts DOB", "Profession actor", "Nationality British"],
        },
        "key_topics": ["Emilia Clarke religion"],
        "subtopics": [],
        "core_content_recommendations": ["Verify latest interview", "Add YouTube embed"],
        "seo_suggestions": ["Focus keyword in H2"],
        "topical_coverage": ["Game of Thrones faith themes"],
        "target_keywords": ["emilia clarke religion"],
    }
    mock_clusters = {
        "primary": "emilia clarke religion",
        "secondary": [],
        "lsi": [],
        "paa": [],
        "long_tail": [],
    }
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value=json.dumps(religious_json),
    ), patch(
        "app.services.openrouter_service.extract_keyword_clusters",
        new_callable=AsyncMock,
        return_value=mock_clusters,
    ):
        result = await generate_research_content_summary(
            query="emilia clarke",
            results=SAMPLE_RESULTS,
            category="Celebrities & Public Figures",
            celebrity_content_type="religious",
        )
    assert result["outline_template"] == "celebrity_religion_v2"
    assert len(result["outline"]) == 12
    assert any("Faith in the Limelight" in n["text"] for n in result["outline"])
    assert result["celebrity_content_type"] == "religious"
    assert result.get("section_notes")


def test_is_celebrity_biography_summary():
    from app.services.celebrity_outline_templates import (
        is_celebrity_biography_summary,
        is_celebrity_religious_summary,
    )

    assert is_celebrity_biography_summary("Celebrities & Public Figures", "biography")
    assert not is_celebrity_biography_summary("Celebrities & Public Figures", "religious")
    assert not is_celebrity_biography_summary("Sports", "biography")
    assert is_celebrity_religious_summary("Celebrities & Public Figures", "religious")


def test_build_celebrity_biography_outline():
    from app.services.celebrity_outline_templates import build_celebrity_biography_outline

    outline = build_celebrity_biography_outline(celebrity_name="NiJaree Canady")
    h2_texts = [n["text"] for n in outline if n["level"] == 2]
    assert "Introduction" in h2_texts
    assert "Early Life" in h2_texts
    assert "Career Overview" in h2_texts
    assert "Conclusion" in h2_texts
    career = next(n for n in outline if n["text"] == "Career Overview")
    child_texts = [c["text"] for c in career.get("children") or []]
    assert "Career Beginnings" in child_texts


@pytest.mark.asyncio
async def test_celebrity_biography_summary_with_news_bundle():
    from app.services.news_research_service import NewsResearchBundle

    bio_json = {
        "summary": "NiJaree Canady won a golden ticket. This outline covers her full biography.",
        "h1": "NiJaree Canady Wins Golden Ticket — Who Is She Really?",
        "section_notes": {
            "early_life": ["Born in Oklahoma", "Softball from young age", "Family support"],
            "career_overview": ["Texas Tech star", "Record-breaking NIL", "All-American"],
        },
        "key_topics": ["NiJaree Canady"],
        "subtopics": [],
        "core_content_recommendations": ["Verify NIL deal sources"],
        "seo_suggestions": ["Use news hook in title"],
        "topical_coverage": ["College softball"],
        "target_keywords": ["nijaree canady", "nijaree canady biography"],
    }
    mock_bundle = NewsResearchBundle(
        query="nijaree canady",
        headline="Golden Ticket Winner\nWho Is She Really?",
        story_description="Canady became the first golden ticket recipient.",
        description="Texas Tech star wins golden ticket.",
        hashtags=["#Softball"],
        news_results=[
            {"title": "News", "link": "https://x.com", "source": "ESPN", "snippet": "Sn", "date": "today"}
        ],
    )
    mock_clusters = {
        "primary": "nijaree canady",
        "secondary": [],
        "lsi": [],
        "paa": [],
        "long_tail": [],
    }
    seo_title = "NiJaree Canady Wins Golden Ticket for AUSL — Full Biography & Untold Facts"
    seo_meta = (
        "Discover NiJaree Canady's 2026 golden ticket story. Learn biography, age, net worth, religion, "
        "career path, and what makes her a top college softball star today."
    )
    with patch(
        "app.services.news_research_service.build_news_bundle",
        new_callable=AsyncMock,
        return_value=mock_bundle,
    ), patch(
        "app.services.openrouter_service.generate_biography_news_seo_title",
        new_callable=AsyncMock,
        return_value=seo_title,
    ), patch(
        "app.services.openrouter_service.generate_biography_news_meta_description",
        new_callable=AsyncMock,
        return_value=seo_meta,
    ), patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value=json.dumps(bio_json),
    ), patch(
        "app.services.openrouter_service.extract_keyword_clusters",
        new_callable=AsyncMock,
        return_value=mock_clusters,
    ):
        result = await generate_research_content_summary(
            query="nijaree canady",
            results=SAMPLE_RESULTS,
            category="Celebrities & Public Figures",
            celebrity_content_type="biography",
        )
    assert result["outline_template"] == "celebrity_biography_v1"
    assert result["celebrity_content_type"] == "biography"
    assert result.get("news_hook_paragraph") == "Canady became the first golden ticket recipient."
    assert result.get("news_headline")
    from app.services.news_research_service import has_biography_hook_tail

    assert has_biography_hook_tail(result.get("news_headline", ""))
    assert "—" in result.get("news_headline", "")
    assert result["h1"] == seo_title
    assert result.get("news_meta_description") == seo_meta
    assert 145 <= len(result.get("news_meta_description", "")) <= 165
    assert result.get("news_context")
    assert len(result["outline"]) >= 8


@pytest.mark.asyncio
async def test_celebrity_biography_summary_fallback_without_news():
    from app.services.openrouter_service import _fallback_celebrity_biography_summary

    with patch(
        "app.services.news_research_service.build_news_bundle",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value="not valid json",
    ), patch(
        "app.services.openrouter_service.extract_keyword_clusters",
        new_callable=AsyncMock,
        return_value={"primary": "jason momoa", "secondary": [], "lsi": [], "paa": [], "long_tail": []},
    ):
        result = await generate_research_content_summary(
            query="jason momoa",
            results=SAMPLE_RESULTS,
            category="Celebrities & Public Figures",
            celebrity_content_type="biography",
        )
    assert result["outline_template"] == "celebrity_biography_v1"
    assert "jason momoa" in result["h1"].lower() or "Jason Momoa" in result["h1"]
    assert not result.get("news_hook_paragraph")

    fallback = _fallback_celebrity_biography_summary("jason momoa", SAMPLE_RESULTS, None)
    assert fallback["outline_template"] == "celebrity_biography_v1"
    assert not fallback.get("news_headline")


def test_is_celebrity_net_worth_summary():
    from app.services.celebrity_outline_templates import (
        is_celebrity_biography_summary,
        is_celebrity_net_worth_summary,
        is_celebrity_religious_summary,
    )

    assert is_celebrity_net_worth_summary("Celebrities & Public Figures", "net_worth")
    assert not is_celebrity_net_worth_summary("Celebrities & Public Figures", "biography")
    assert not is_celebrity_net_worth_summary("Sports", "net_worth")
    assert is_celebrity_biography_summary("Celebrities & Public Figures", "biography")
    assert is_celebrity_religious_summary("Celebrities & Public Figures", "religious")


def test_build_celebrity_net_worth_outline():
    from app.services.celebrity_outline_templates import build_celebrity_net_worth_outline

    outline = build_celebrity_net_worth_outline(celebrity_name="Jutta Leerdam")
    assert len(outline) == 6
    h2_texts = [n["text"] for n in outline if n["level"] == 2]
    assert h2_texts[0] == "Introduction"
    assert "Quick Facts" in h2_texts[1]
    assert "Jutta Leerdam Net Worth & Income Sources" in h2_texts[2]
    assert "How Jutta Leerdam's Net Worth Is Estimated" in h2_texts
    assert "Career & Achievements" in h2_texts
    assert "Personal Life" in h2_texts
    assert "Early Life & Background" not in h2_texts
    assert not any("FAQ" in t for t in h2_texts)

    income = next(n for n in outline if "Net Worth & Income Sources" in n["text"])
    child_texts = [c["text"] for c in income.get("children") or []]
    assert "Career Earnings & Salary" in child_texts
    assert "Media, Endorsements & Public Appearances" in child_texts
    assert "Investments, Real Estate & Other Assets" in child_texts

    career_idx = h2_texts.index("Career & Achievements")
    personal_idx = h2_texts.index("Personal Life")
    assert career_idx < personal_idx


def test_net_worth_quality_rules():
    from app.services.celebrity_outline_templates import get_net_worth_quality_rules

    rules = get_net_worth_quality_rules("Jutta Leerdam")
    assert "Jutta Leerdam Net Worth" in rules
    assert "religion" in rules.lower()
    assert "FAQ" in rules or "net worth" in rules.lower()
    assert "do not" in rules.lower() and "faq" in rules.lower()
    assert "not publicly disclosed" in rules.lower()


def test_outline_to_net_worth_prompt_block_excludes_faq():
    from app.services.celebrity_outline_templates import outline_to_net_worth_prompt_block

    block = outline_to_net_worth_prompt_block("Jutta Leerdam")
    assert "Jutta Leerdam Net Worth & Income Sources" in block
    assert "How Jutta Leerdam's Net Worth Is Estimated" in block
    assert "Media, Endorsements & Public Appearances" in block
    assert "do not" in block.lower() and "faq" in block.lower()
    assert "## FAQ" not in block
    assert "omit" in block.lower() or "only include a row" in block.lower()
    assert "1,150" in block or "1150" in block


@pytest.mark.asyncio
async def test_celebrity_net_worth_summary_uses_template_outline():
    from app.services.openrouter_service import _fallback_celebrity_net_worth_summary

    net_worth_json = {
        "summary": "Jutta Leerdam has an estimated net worth of $5 million as of 2026, built from speed skating prize money, Team Jumbo-Visma salary, and brand deals with Dior and Hugo Boss.",
        "h1": "Jutta Leerdam Net Worth 2026: How She Built Her $5M Fortune",
        "section_notes": {
            "intro": ["$5M net worth as of 2026", "Primary: prize money + endorsements", "Salary €600k–€900k"],
            "career_earnings": ["Olympic gold 2026", "World Championship payouts", "Team Jumbo-Visma contract"],
            "media_appearances": ["Dior partnership", "Hugo Boss deal", "Omega sponsorship"],
            "investments_assets": ["No major disclosed real estate", "Team contract residual value"],
            "methodology": ["Prize money plus endorsements", "Public contract reports"],
        },
        "key_topics": ["Jutta Leerdam net worth"],
        "subtopics": [],
        "core_content_recommendations": ["Verify endorsement values", "Cite salary sources"],
        "seo_suggestions": ["Lead with net worth figure"],
        "topical_coverage": ["Speed skating earnings"],
        "target_keywords": ["jutta leerdam net worth"],
    }
    mock_clusters = {
        "primary": "jutta leerdam net worth",
        "secondary": [],
        "lsi": [],
        "paa": ["How much is Jutta Leerdam worth?"],
        "long_tail": [],
    }
    with patch(
        "app.services.openrouter_service.generate_content",
        new_callable=AsyncMock,
        return_value=json.dumps(net_worth_json),
    ), patch(
        "app.services.openrouter_service.extract_keyword_clusters",
        new_callable=AsyncMock,
        return_value=mock_clusters,
    ):
        result = await generate_research_content_summary(
            query="jutta leerdam",
            results=SAMPLE_RESULTS,
            category="Celebrities & Public Figures",
            celebrity_content_type="net_worth",
        )
    assert result["outline_template"] == "celebrity_net_worth_v2"
    assert result["celebrity_content_type"] == "net_worth"
    assert "net worth" in result["h1"].lower()
    assert "net worth" in result["summary"].lower() or "$" in result["summary"]
    assert len(result["outline"]) == 6
    assert any("Net Worth & Income Sources" in n["text"] for n in result["outline"])
    assert result.get("section_notes")
    assert "jutta leerdam net worth" in result["target_keywords"][0].lower()


@pytest.mark.asyncio
async def test_celebrity_net_worth_summary_fallback():
    from app.services.openrouter_service import _fallback_celebrity_net_worth_summary

    with patch(
        "app.services.openrouter_service.generate_content",
        new_callable=AsyncMock,
        return_value="not valid json",
    ), patch(
        "app.services.openrouter_service.extract_keyword_clusters",
        new_callable=AsyncMock,
        return_value={"primary": "jutta leerdam", "secondary": [], "lsi": [], "paa": [], "long_tail": []},
    ):
        result = await generate_research_content_summary(
            query="jutta leerdam net worth",
            results=SAMPLE_RESULTS,
            category="Celebrities & Public Figures",
            celebrity_content_type="net_worth",
        )
    assert result["outline_template"] == "celebrity_net_worth_v2"
    assert "net worth" in result["h1"].lower()

    fallback = _fallback_celebrity_net_worth_summary("jutta leerdam", SAMPLE_RESULTS)
    assert fallback["outline_template"] == "celebrity_net_worth_v2"
    assert fallback["celebrity_content_type"] == "net_worth"
    assert "Jutta Leerdam" in fallback["h1"]
