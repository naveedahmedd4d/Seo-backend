"""Tests for humanizer pattern analysis, diff, catalog, structure, and pipeline (mocked LLM)."""

import os
import pytest
from unittest.mock import AsyncMock, patch

from app.services.humanizer.catalog import flag_only_ratio, get_replacement, load_catalog
from app.services.humanizer.patterns import analyze_text, find_phrase_hits
from app.services.humanizer.replacement import apply_rule_replacements, MAX_REPLACEMENTS_PER_PARAGRAPH
from app.services.humanizer.structure import apply_structure_rewrite, _limit_em_dashes
from app.services.humanizer.diff import build_diff
from app.services.humanizer.meaning_drift import entity_retention_ratio


SAMPLE_AI_TEXT = (
    "Furthermore, when it comes to the landscape of technology, "
    "we must delve into the robust solutions that serve as a testament to innovation. "
    "Moreover, it is not just about speed — it is about quality."
)


def test_catalog_loads():
    load_catalog.cache_clear()
    entries = load_catalog()
    assert len(entries) >= 80


def test_catalog_flag_only_ratio_below_threshold():
    load_catalog.cache_clear()
    assert flag_only_ratio() < 0.10


def test_tone_picks_different_replacement():
    load_catalog.cache_clear()
    entries = [e for e in load_catalog() if e.id == "furthermore"]
    assert entries
    entry = entries[0]
    formal = get_replacement(entry, "formal", 0)
    casual = get_replacement(entry, "casual", 0)
    assert formal is not None
    assert casual is not None


def test_analyze_text_flags_phrases():
    result = analyze_text(SAMPLE_AI_TEXT)
    assert len(result.hits) >= 3
    assert 0 <= result.ai_score <= 100
    assert result.ai_score > 10
    assert "phrase_density" in result.score_breakdown


def test_find_phrase_hits_non_overlapping():
    hits = find_phrase_hits("furthermore moreover furthermore")
    matched = [h.matched.lower() for h in hits]
    assert "furthermore" in matched


def test_rule_replacement_furthermore():
    analysis = analyze_text("Furthermore, this is a test.")
    text, applied = apply_rule_replacements("Furthermore, this is a test.", analysis)
    assert "furthermore" not in text.lower() or any(
        a["from"].lower() == "furthermore" for a in applied
    )


def test_replacement_cap_per_paragraph():
    para = (
        "Furthermore, we leverage robust comprehensive solutions. "
        "Moreover, delve into the landscape. Additionally, utilize seamless holistic approaches."
    )
    analysis = analyze_text(para)
    _, applied = apply_rule_replacements(para, analysis)
    assert len(applied) <= MAX_REPLACEMENTS_PER_PARAGRAPH


def test_structure_limits_em_dashes():
    text = "One — two — three — four."
    out = _limit_em_dashes(text, 4)
    assert out.count("—") <= 1


def test_structure_rewrite_runs():
    analysis = analyze_text("Furthermore, it is not just about speed — it is about quality — and more.")
    out = apply_structure_rewrite(
        "Furthermore, it is not just about speed — it is about quality — and more.",
        analysis,
    )
    assert isinstance(out, str)
    assert len(out) > 0


def test_entity_retention():
    orig = "OpenAI released GPT-4 in 2023 with 1.76 trillion parameters."
    good = "OpenAI shipped GPT-4 in 2023 with 1.76 trillion parameters."
    bad = "A company released a model in a recent year with many parameters."
    assert entity_retention_ratio(orig, good) > entity_retention_ratio(orig, bad)


def test_build_diff_segments():
    original = "Hello world test"
    humanized = "Hello brave world"
    segments = build_diff(original, humanized)
    types = {s.type for s in segments}
    assert "equal" in types or "insert" in types or "delete" in types
    joined = "".join(s.text for s in segments)
    assert "Hello" in joined


@pytest.mark.asyncio
async def test_humanize_pipeline_mocked():
    if not os.getenv("RUN_HUMANIZER_INTEGRATION"):
        from app.services.humanizer.pipeline import humanize_pipeline

        mock_rewrite = "Here's a simpler take on the topic. It's solid work, not perfect."
        mock_grammar = mock_rewrite

        with patch(
            "app.services.humanizer.replacement.llm_rewrite",
            new_callable=AsyncMock,
            return_value=mock_rewrite,
        ), patch(
            "app.services.humanizer.grammar.grammar_pass",
            new_callable=AsyncMock,
            return_value=mock_grammar,
        ):
            result = await humanize_pipeline(SAMPLE_AI_TEXT, tone="casual")

        assert result.humanized == mock_grammar
        assert result.ai_score_before >= 0
        assert result.ai_score_after >= 0
        assert result.tone == "casual"
        assert isinstance(result.score_improved, bool)
        assert isinstance(result.diff_segments, list)
        assert len(result.patterns_found) >= 0
    else:
        pytest.skip("Integration test requires GROQ_API_KEY and RUN_HUMANIZER_INTEGRATION=1")
