"""News outline templates — section slugs, word budgets, heading structure."""

from app.services.news_outline_templates import (
    NEWS_EXPLAINER_INTENT,
    NEWS_EXPLAINER_TEMPLATE_ID,
    NEWS_OPINION_INTENT,
    NEWS_OPINION_TEMPLATE_ID,
    NEWS_TIMELINE_INTENT,
    NEWS_TIMELINE_TEMPLATE_ID,
    get_news_explainer_section_slugs,
    get_news_explainer_template,
    get_news_opinion_section_slugs,
    get_news_opinion_template,
    get_news_timeline_section_slugs,
    get_news_timeline_template,
    is_any_news_request,
    is_news_explainer_request,
    is_news_opinion_request,
    is_news_timeline_request,
    outline_to_news_explainer_prompt_block,
    outline_to_news_opinion_prompt_block,
    outline_to_news_timeline_prompt_block,
)


def test_timeline_template_has_required_slugs():
    slugs = get_news_timeline_section_slugs()
    assert slugs == [
        "what_happened",
        "background",
        "timeline",
        "why_it_matters",
        "reactions",
        "current_status",
        "whats_next",
    ]


def test_explainer_template_has_required_slugs():
    slugs = get_news_explainer_section_slugs()
    assert slugs == [
        "intro",
        "background",
        "key_terms",
        "deep_dive",
        "real_examples",
        "perspectives",
    ]


def test_opinion_template_has_required_slugs():
    slugs = get_news_opinion_section_slugs()
    assert slugs == [
        "what_happened",
        "the_reaction",
        "context",
        "why_it_wont_go_away",
        "our_take",
    ]


def test_timeline_word_budgets():
    sections = {s.slug: s for s in get_news_timeline_template("Pete Hegseth")}
    assert sections["what_happened"].word_budget == (130, 160)
    assert sections["timeline"].word_budget == (300, 500)
    assert sections["whats_next"].word_budget == (80, 100)


def test_explainer_word_budgets():
    sections = {s.slug: s for s in get_news_explainer_template("LIV Golf contracts")}
    assert sections["intro"].word_budget == (120, 150)
    assert sections["deep_dive"].word_budget == (200, 280)
    assert sections["key_terms"].word_budget == (120, 150)


def test_opinion_word_budgets():
    sections = {s.slug: s for s in get_news_opinion_template("Kristi Noem")}
    assert sections["the_reaction"].word_budget == (200, 250)
    assert sections["our_take"].word_budget == (60, 80)


def test_timeline_headings_are_news_not_biography():
    headings = [s.heading for s in get_news_timeline_template("Pete Hegseth")]
    joined = " ".join(headings).lower()
    assert "early life" not in joined
    assert "career overview" not in joined
    assert "first leading role" not in joined
    assert "What Happened" in headings
    assert "Timeline of Events" in headings


def test_timeline_prompt_block_bans_biography_and_faq():
    block = outline_to_news_timeline_prompt_block("Pete Hegseth")
    assert "What Happened" in block
    assert "Timeline of Events" in block
    assert "faq" in block.lower()
    assert "do not" in block.lower() or "do NOT" in block
    assert "Early Life" not in block or "Do NOT write Early Life" in block


def test_explainer_prompt_block_requires_key_terms_table():
    block = outline_to_news_explainer_prompt_block("WNBA salary cap")
    assert "Key Terms" in block
    assert "markdown table" in block.lower()
    assert "Term |" in block or "Term |" in block.replace(" ", "") or "Term |" in block


def test_opinion_prompt_block_requires_editorial_label():
    block = outline_to_news_opinion_prompt_block("Kristi Noem")
    assert "Our Take" in block
    assert "Editorial opinion" in block
    assert "The Reaction" in block


def test_intent_detectors():
    assert is_news_timeline_request("news_timeline")
    assert is_news_explainer_request("news_explainer")
    assert is_news_opinion_request("news_opinion")
    assert is_any_news_request("news_timeline")
    assert is_any_news_request("news_explainer")
    assert is_any_news_request("news_opinion")
    assert not is_news_timeline_request("cluster_hub")
    assert not is_any_news_request("cluster_hub")
    assert not is_any_news_request(None)
    assert not is_any_news_request("parent_overview")


def test_template_ids_and_intents_are_stable():
    assert NEWS_TIMELINE_TEMPLATE_ID == "news_timeline_v1"
    assert NEWS_EXPLAINER_TEMPLATE_ID == "news_explainer_v1"
    assert NEWS_OPINION_TEMPLATE_ID == "news_opinion_v1"
    assert NEWS_TIMELINE_INTENT == "news_timeline"
    assert NEWS_EXPLAINER_INTENT == "news_explainer"
    assert NEWS_OPINION_INTENT == "news_opinion"
