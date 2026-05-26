"""
Stage 3: Grammar-only polish via Groq (low temperature).
"""

from __future__ import annotations

import re

from app.services.openrouter_service import BLOG_GENERATION_MODEL_GROQ, generate_content

GRAMMAR_SYSTEM = """You are a copy editor. Fix grammar, spelling, and punctuation only.

Rules:
- Do NOT change meaning, facts, names, dates, or numbers.
- Do NOT rewrite for style or "humanize" — only fix clear errors.
- Keep the same voice, contractions, and sentence rhythm.
- Output ONLY the corrected text. No preamble or commentary."""


async def grammar_pass(text: str) -> str:
    if not text or not str(text).strip():
        return text

    words = len(re.findall(r"\b\w+\b", text))
    if words < 20:
        return text.strip()

    user = (
        "Correct grammar and punctuation only. Leave wording and style as-is unless clearly wrong.\n\n"
        + str(text).strip()
    )

    result = await generate_content(
        user,
        model=BLOG_GENERATION_MODEL_GROQ,
        system=GRAMMAR_SYSTEM,
        max_tokens=8192,
        temperature=0.2,
        top_p=0.9,
        timeout_seconds=90.0,
    )
    cleaned = result.strip()
    # Strip accidental wrappers
    if cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) > 2:
        cleaned = cleaned[1:-1]
    return cleaned
