"""
Humanizer pipeline — Apify Content Humanizer (akash9078/ai-content-humanizer).

Local heuristic scores + diff are kept for the dashboard UI only.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.schemas.ai_humanizer import HumanizeResponse, PatternHitSchema, Tone
from app.services.apify_humanizer_service import humanize_text_apify
from app.services.humanizer.diff import build_diff
from app.services.humanizer.patterns import analyze_text

logger = logging.getLogger(__name__)


def _hits_to_schema(hits) -> list[PatternHitSchema]:
    return [
        PatternHitSchema(
            category=h.category,
            matched=h.matched,
            start=h.start,
            end=h.end,
        )
        for h in hits
    ]


async def humanize_pipeline(
    text: str,
    tone: Tone = "professional",
    voice_sample: Optional[str] = None,
    use_translation_chain: Optional[bool] = None,
    *,
    api_key: Optional[str] = None,
) -> HumanizeResponse:
    """Humanize via Apify; tone/voice_sample ignored (actor has no tone API)."""
    del voice_sample, use_translation_chain

    if not text or not str(text).strip():
        raise ValueError("Text is empty")

    original = str(text).strip()
    analysis_before = analyze_text(original, tone=tone)

    humanized = await humanize_text_apify(original, api_key=api_key)

    analysis_after = analyze_text(humanized, tone=tone)
    diff_segments = build_diff(original, humanized)
    score_improved = analysis_after.ai_score < analysis_before.ai_score

    if not score_improved and analysis_after.ai_score > 25:
        logger.warning(
            "Heuristic AI score did not improve: before=%.1f after=%.1f tone=%s",
            analysis_before.ai_score,
            analysis_after.ai_score,
            tone,
        )

    return HumanizeResponse(
        humanized=humanized,
        ai_score_before=analysis_before.ai_score,
        ai_score_after=analysis_after.ai_score,
        score_improved=score_improved,
        tone=tone,
        score_breakdown_before=analysis_before.score_breakdown,
        score_breakdown_after=analysis_after.score_breakdown,
        patterns_found=_hits_to_schema(analysis_before.hits),
        replacements_applied=[],
        diff_segments=diff_segments,
        method="apify-akash9078-ai-content-humanizer",
    )
