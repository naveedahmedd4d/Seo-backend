"""
Stage 2: Rule-based replacements, structure tweaks, then LLM rewrite.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from app.services.humanizer.catalog import (
    get_entries_sorted,
    get_entry_by_id,
    get_replacement,
)
from app.services.humanizer.meaning_drift import check_meaning_drift
from app.services.humanizer.patterns import AnalysisResult, analyze_text, format_analysis_for_prompt
from app.services.humanizer.prompts import build_humanize_system_prompt
from app.services.humanizer.structure import apply_structure_rewrite
from app.services.openrouter_service import BLOG_GENERATION_MODEL_GROQ, generate_content

MAX_REPLACEMENTS_PER_PARAGRAPH = 3


def _preserve_case(original: str, replacement: str) -> str:
    if not replacement:
        return original
    if original.isupper():
        return replacement.upper()
    if original[0].isupper():
        return replacement[0].upper() + replacement[1:] if len(replacement) > 1 else replacement.upper()
    return replacement.lower()


def _is_protected_span(text: str, start: int, end: int) -> bool:
    """Skip replacements inside quotes or ALL-CAPS acronyms."""
    window_start = max(0, start - 80)
    window_end = min(len(text), end + 80)
    window = text[window_start:window_end]
    rel_start = start - window_start
    rel_end = end - window_start

    # Inside double quotes
    before = window[:rel_start]
    if before.count('"') % 2 == 1:
        return True
    if before.count("'") % 2 == 1:
        return True

    matched = text[start:end]
    if matched.isupper() and len(matched) >= 2 and matched.isalpha():
        return True
    return False


def _split_paragraphs(text: str) -> List[str]:
    if "\n\n" in text:
        return text.split("\n\n")
    return [text]


def _join_paragraphs(parts: List[str]) -> str:
    return "\n\n".join(parts)


def apply_rule_replacements(
    text: str,
    analysis: AnalysisResult,
    tone: str = "professional",
) -> Tuple[str, List[Dict[str, str]]]:
    """Replace flagged phrases with tone-aware catalog synonyms (capped per paragraph)."""
    applied: List[Dict[str, str]] = []
    paragraphs = _split_paragraphs(text)
    result_parts: List[str] = []

    for para_idx, para in enumerate(paragraphs):
        swap_count = 0
        result = para

        para_analysis = analyze_text(para, tone=tone)
        replacements_done: set[str] = set()

        for hit in sorted(para_analysis.hits, key=lambda h: -len(h.matched)):
            if swap_count >= MAX_REPLACEMENTS_PER_PARAGRAPH:
                break
            if not hit.suggestion or not hit.entry_id:
                continue
            key = hit.matched.lower()
            if key in replacements_done:
                continue

            entry = get_entry_by_id(hit.entry_id)
            if entry and entry.llm_only:
                continue

            pattern = re.compile(re.escape(hit.matched), re.IGNORECASE)

            def repl(m: re.Match, sug: str = hit.suggestion) -> str:
                nonlocal swap_count
                if _is_protected_span(result, m.start(), m.end()):
                    return m.group(0)
                if swap_count >= MAX_REPLACEMENTS_PER_PARAGRAPH:
                    return m.group(0)
                orig = m.group(0)
                new = _preserve_case(orig, sug)
                applied.append({"from": orig, "to": new})
                swap_count += 1
                replacements_done.add(key)
                return new

            result = pattern.sub(repl, result, count=1)

        # Second pass: catalog entries with suggestions not yet hit
        lower_result = result.lower()
        for entry in get_entries_sorted():
            if swap_count >= MAX_REPLACEMENTS_PER_PARAGRAPH:
                break
            if entry.llm_only:
                continue
            suggestion = get_replacement(entry, tone, para_idx)
            if not suggestion:
                continue
            if entry.pattern.lower() not in lower_result:
                continue
            if entry.pattern.lower() in replacements_done:
                continue
            pattern = re.compile(re.escape(entry.pattern), re.IGNORECASE)

            def repl2(m: re.Match, sug: str = suggestion, ent=entry) -> str:
                nonlocal swap_count
                if _is_protected_span(result, m.start(), m.end()):
                    return m.group(0)
                if swap_count >= MAX_REPLACEMENTS_PER_PARAGRAPH:
                    return m.group(0)
                orig = m.group(0)
                new = _preserve_case(orig, sug)
                if not any(a["from"] == orig and a["to"] == new for a in applied):
                    applied.append({"from": orig, "to": new})
                swap_count += 1
                replacements_done.add(ent.pattern.lower())
                return new

            result = pattern.sub(repl2, result, count=1)
            lower_result = result.lower()

        result_parts.append(result)

    return _join_paragraphs(result_parts), applied


async def llm_rewrite(
    text: str,
    analysis: Optional[AnalysisResult] = None,
    tone: str = "professional",
    original_for_drift: Optional[str] = None,
) -> str:
    """Groq LLM rewrite with tone-specific prompt and optional drift check."""
    if not text or not str(text).strip():
        raise ValueError("Text is empty")

    if analysis is None:
        analysis = analyze_text(text, tone=tone)

    analysis_notes = format_analysis_for_prompt(analysis)
    system = build_humanize_system_prompt(analysis_notes, tone=tone)
    user = (
        "Rewrite the following. Change the voice and sentence flow completely; keep the meaning and facts. "
        "It must not read like a motivational three-act mini-essay.\n\n"
        + str(text).strip()
    )

    result = await generate_content(
        user,
        model=BLOG_GENERATION_MODEL_GROQ,
        system=system,
        max_tokens=8192,
        temperature=0.88,
        top_p=0.92,
        frequency_penalty=0.62,
        presence_penalty=0.42,
        timeout_seconds=120.0,
    )

    if original_for_drift:
        check_meaning_drift(original_for_drift, result, tone=tone)

    return result


async def run_replacement_stage(
    text: str,
    analysis: AnalysisResult,
    tone: str = "professional",
) -> Tuple[str, List[Dict[str, str]]]:
    """Rules → structure → LLM."""
    after_rules, applied = apply_rule_replacements(text, analysis, tone=tone)
    post_rules_analysis = analyze_text(after_rules, tone=tone)
    after_structure = apply_structure_rewrite(after_rules, post_rules_analysis, tone=tone)
    post_analysis = analyze_text(after_structure, tone=tone)
    rewritten = await llm_rewrite(
        after_structure,
        post_analysis,
        tone=tone,
        original_for_drift=text,
    )
    return rewritten.strip(), applied
