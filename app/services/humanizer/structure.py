"""
Stage 3: Conservative deterministic structure edits (no heavy LLM-style reshaping).
"""

from __future__ import annotations

import re
from typing import List

from app.services.humanizer.patterns import CONTRACTION_PAIRS, AnalysisResult

_TRANSITION_OPENERS = [
    (r"^Furthermore,\s*", "And "),
    (r"^Moreover,\s*", "And "),
    (r"^Additionally,\s*", "Also "),
    (r"^Consequently,\s*", "So "),
    (r"^Nevertheless,\s*", "Still, "),
    (r"^Nonetheless,\s*", "Still, "),
]

_OPENER_PREFIXES = ["Still,", "That said,", "Meanwhile,"]


def _preserve_case(original: str, replacement: str) -> str:
    if not replacement:
        return original
    if original.isupper():
        return replacement.upper()
    if original[0].isupper():
        return replacement[0].upper() + replacement[1:] if len(replacement) > 1 else replacement.upper()
    return replacement.lower()


def _cull_transition_openers(text: str, transition_count: float) -> str:
    if transition_count < 2:
        return text
    paragraphs = text.split("\n\n")
    out: List[str] = []
    for para in paragraphs:
        lines = para.split("\n")
        new_lines = []
        for line in lines:
            updated = line
            for pattern, repl in _TRANSITION_OPENERS:
                updated = re.sub(pattern, repl, updated, count=1, flags=re.IGNORECASE)
            new_lines.append(updated)
        out.append("\n".join(new_lines))
    return "\n\n".join(out)


def _split_long_sentence(text: str, sent_std: float) -> str:
    if sent_std >= 3.5 or len(text) <= 120:
        return text
    for m in re.finditer(r"[^.!?]+[.!?]", text):
        sent = m.group(0)
        if len(sent) > 100 and "," in sent:
            idx = sent.find(",", 50)
            if idx > 0 and idx < len(sent) - 5:
                part1 = sent[: idx + 1].strip()
                part2 = sent[idx + 1 :].strip()
                if part2 and part2[0].islower():
                    part2 = part2[0].upper() + part2[1:]
                new_sent = part1 + " " + part2
                return text[: m.start()] + new_sent + text[m.end() :]
    return text


def _apply_contractions(text: str, contraction_ratio: float, tone: str, max_count: int) -> str:
    if contraction_ratio >= 0.25:
        return text
    limit = 2 if tone == "formal" else max_count
    result = text
    applied = 0
    for pattern, replacement in CONTRACTION_PAIRS:
        if applied >= limit:
            break

        def repl(m: re.Match, rep: str = replacement) -> str:
            nonlocal applied
            applied += 1
            return _preserve_case(m.group(0), rep)

        result = re.sub(pattern, repl, result, count=1, flags=re.IGNORECASE)
    return result


def _vary_openers(text: str, opener_uniformity: float) -> str:
    if opener_uniformity < 0.5:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if len(sentences) < 3:
        return text
    openers: dict[str, int] = {}
    for s in sentences:
        m = re.match(r"^(\w+)", s)
        if m:
            w = m.group(1).lower()
            openers[w] = openers.get(w, 0) + 1
    repeat_word = None
    for w, c in openers.items():
        if c >= 2:
            repeat_word = w
            break
    if not repeat_word:
        return text
    prefix = _OPENER_PREFIXES[hash(text) % len(_OPENER_PREFIXES)]
    seen_repeat = 0
    rebuilt: List[str] = []
    for s in sentences:
        m = re.match(r"^(\w+)", s)
        if m and m.group(1).lower() == repeat_word:
            seen_repeat += 1
            if seen_repeat == 2:
                rebuilt.append(f"{prefix} {s}")
                continue
        rebuilt.append(s)
    return " ".join(rebuilt)


def _limit_em_dashes(text: str, em_count: float) -> str:
    if em_count <= 1:
        return text
    count = 0

    def repl_em(m: re.Match) -> str:
        nonlocal count
        count += 1
        if count == 1:
            return m.group(0)
        return ", "

    text = re.sub(r"—", repl_em, text)
    count = 0

    def repl_dd(m: re.Match) -> str:
        nonlocal count
        count += 1
        if count == 1:
            return m.group(0)
        return ", "

    return re.sub(r"--", repl_dd, text)


def apply_structure_rewrite(
    text: str,
    analysis: AnalysisResult,
    tone: str = "professional",
) -> str:
    """Apply safe structure tweaks based on analysis symptoms."""
    if not text or not str(text).strip():
        return text

    symptoms = analysis.symptoms
    result = text

    result = _cull_transition_openers(
        result, symptoms.get("transition_count", 0)
    )
    result = _split_long_sentence(
        result, symptoms.get("sentence_length_std", 10)
    )
    result = _apply_contractions(
        result,
        symptoms.get("contraction_ratio", 0.5),
        tone,
        max_count=3,
    )
    result = _vary_openers(result, symptoms.get("opener_uniformity", 0))
    result = _limit_em_dashes(result, symptoms.get("em_dash_count", 0))

    return result
