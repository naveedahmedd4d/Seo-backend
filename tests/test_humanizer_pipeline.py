"""Tests for Apify-backed humanizer pipeline."""

import os
import pytest
from unittest.mock import AsyncMock, patch

from app.services.humanizer.patterns import analyze_text
from app.services.humanizer.diff import build_diff
from app.services.apify_humanizer_service import _extract_humanized

SAMPLE = (
    "Furthermore, when it comes to the landscape of technology, "
    "we must delve into robust solutions."
)


def test_extract_humanized_prefers_humanized_content():
    item = {"humanizedContent": "Natural text.", "humanized": "Other"}
    assert _extract_humanized(item) == "Natural text."


def test_extract_humanized_fallback_humanized():
    assert _extract_humanized({"humanized": "Done."}) == "Done."


def test_analyze_text_runs():
    result = analyze_text(SAMPLE)
    assert 0 <= result.ai_score <= 100


@pytest.mark.asyncio
async def test_humanize_pipeline_mocked_apify():
    if os.getenv("RUN_HUMANIZER_INTEGRATION"):
        pytest.skip("Use RUN_HUMANIZER_INTEGRATION=1 for live Apify test")

    from app.services.humanizer.pipeline import humanize_pipeline

    mock_out = "Here's a simpler take on technology and robust tools."

    with patch(
        "app.services.humanizer.pipeline.humanize_text_apify",
        new_callable=AsyncMock,
        return_value=mock_out,
    ):
        result = await humanize_pipeline(SAMPLE, tone="casual")

    assert result.humanized == mock_out
    assert result.method == "apify-akash9078-ai-content-humanizer"
    assert result.tone == "casual"
    assert isinstance(result.diff_segments, list)
    joined = "".join(s.text for s in result.diff_segments)
    assert "simpler" in joined or len(joined) > 0


def test_build_diff():
    segments = build_diff("Hello world", "Hello brave world")
    assert any(s.type in ("equal", "insert", "delete") for s in segments)
