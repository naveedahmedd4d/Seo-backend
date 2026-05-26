"""
Orchestrates Stage 1 → 2 → 3 humanizer pipeline.
"""

from __future__ import annotations

import logging

from app.schemas.ai_humanizer import HumanizeResponse, PatternHitSchema, Tone
from app.services.humanizer.diff import build_diff
from app.services.humanizer.grammar import grammar_pass
from app.services.humanizer.patterns import analyze_text
from app.services.humanizer.replacement import run_replacement_stage

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


async def humanize_pipeline(text: str, tone: Tone = "professional") -> HumanizeResponse:
    if not text or not str(text).strip():
        raise ValueError("Text is empty")

    original = str(text).strip()
    analysis_before = analyze_text(original, tone=tone)

    rewritten, replacements_applied = await run_replacement_stage(
        original, analysis_before, tone=tone
    )
    final = await grammar_pass(rewritten)

    analysis_after = analyze_text(final, tone=tone)
    diff_segments = build_diff(original, final)

    score_improved = analysis_after.ai_score < analysis_before.ai_score
    if (
        not score_improved
        and analysis_after.ai_score >= analysis_before.ai_score
        and analysis_after.ai_score > 25
    ):
        logger.warning(
            "Humanizer score did not improve: before=%.1f after=%.1f tone=%s",
            analysis_before.ai_score,
            analysis_after.ai_score,
            tone,
        )

    return HumanizeResponse(
        humanized=final,
        ai_score_before=analysis_before.ai_score,
        ai_score_after=analysis_after.ai_score,
        score_improved=score_improved,
        tone=tone,
        score_breakdown_before=analysis_before.score_breakdown,
        score_breakdown_after=analysis_after.score_breakdown,
        patterns_found=_hits_to_schema(analysis_before.hits),
        replacements_applied=replacements_applied,
        diff_segments=diff_segments,
    )
