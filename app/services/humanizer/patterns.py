"""
AI-tell pattern catalog and heuristic analysis (Stage 1).

Phrase data lives in catalog.yaml; loaded via catalog.py.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.services.humanizer.catalog import (
    get_entries_sorted,
    get_replacement,
    get_transition_words,
)

CONTRACTION_PAIRS = [
    (r"\bdo not\b", "don't"),
    (r"\bdoes not\b", "doesn't"),
    (r"\bdid not\b", "didn't"),
    (r"\bcan not\b", "can't"),
    (r"\bcannot\b", "can't"),
    (r"\bwill not\b", "won't"),
    (r"\bwould not\b", "wouldn't"),
    (r"\bcould not\b", "couldn't"),
    (r"\bshould not\b", "shouldn't"),
    (r"\bis not\b", "isn't"),
    (r"\bare not\b", "aren't"),
    (r"\bwas not\b", "wasn't"),
    (r"\bwere not\b", "weren't"),
    (r"\bhave not\b", "haven't"),
    (r"\bhas not\b", "hasn't"),
    (r"\bhad not\b", "hadn't"),
    (r"\bit is\b", "it's"),
    (r"\bthat is\b", "that's"),
    (r"\bthere is\b", "there's"),
    (r"\bwhat is\b", "what's"),
    (r"\bwho is\b", "who's"),
    (r"\bI am\b", "I'm"),
    (r"\bwe are\b", "we're"),
    (r"\bthey are\b", "they're"),
    (r"\byou are\b", "you're"),
    (r"\bhe is\b", "he's"),
    (r"\bshe is\b", "she's"),
]

WEIGHT_PHRASE_DENSITY = 35.0
WEIGHT_TRANSITION_DENSITY = 15.0
WEIGHT_EM_DASH = 12.0
WEIGHT_LOW_CONTRACTION = 12.0
WEIGHT_LOW_SENTENCE_VARIANCE = 14.0
WEIGHT_UNIFORM_OPENERS = 12.0


@dataclass
class PatternHit:
    category: str
    matched: str
    start: int
    end: int
    suggestion: Optional[str] = None
    entry_id: Optional[str] = None


@dataclass
class AnalysisResult:
    hits: List[PatternHit] = field(default_factory=list)
    symptoms: Dict[str, float] = field(default_factory=dict)
    ai_score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    word_count: int = 0


def _word_count(text: str) -> int:
    words = re.findall(r"\b\w+\b", text)
    return max(len(words), 1)


def _sentence_lengths(text: str) -> List[int]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    lengths = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        lengths.append(len(re.findall(r"\b\w+\b", s)))
    return lengths if lengths else [0]


def _sentence_length_std(text: str) -> float:
    lengths = _sentence_lengths(text)
    if len(lengths) < 2:
        return 0.0
    mean = sum(lengths) / len(lengths)
    variance = sum((x - mean) ** 2 for x in lengths) / len(lengths)
    return math.sqrt(variance)


def _em_dash_count(text: str) -> int:
    return text.count("—") + text.count("--")


def _contraction_ratio(text: str) -> float:
    contractions = len(
        re.findall(
            r"\b\w+'(?:t|re|ve|ll|d|s|m)\b",
            text,
            re.IGNORECASE,
        )
    )
    expansion_candidates = len(
        re.findall(
            r"\b(?:do|does|did|can|will|would|could|should|is|are|was|were|have|has|had|it|that|there|I|we|they|you|he|she)\s+not\b",
            text,
            re.IGNORECASE,
        )
    ) + len(re.findall(r"\b(?:it|that|there|I|we|they|you|he|she)\s+is\b", text, re.IGNORECASE))
    opportunities = contractions + expansion_candidates
    if opportunities == 0:
        return 0.5
    return contractions / max(opportunities, 1)


def _uniform_opener_score(text: str) -> float:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if len(sentences) < 3:
        return 0.0
    openers: Dict[str, int] = {}
    for s in sentences:
        m = re.match(r"^(\w+)", s)
        if m:
            w = m.group(1).lower()
            openers[w] = openers.get(w, 0) + 1
    if not openers:
        return 0.0
    max_repeat = max(openers.values())
    return min(1.0, (max_repeat - 1) / max(len(sentences) - 1, 1))


def _paragraph_index_for_offset(text: str, offset: int) -> int:
    return text[:offset].count("\n\n")


def find_phrase_hits(text: str, tone: str = "professional") -> List[PatternHit]:
    """Find non-overlapping phrase matches (longest first)."""
    hits: List[PatternHit] = []
    occupied: List[Tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        for a, b in occupied:
            if start < b and end > a:
                return True
        return False

    lower = text.lower()
    for entry in get_entries_sorted():
        pattern = re.escape(entry.pattern.lower())
        for m in re.finditer(pattern, lower):
            start, end = m.start(), m.end()
            if overlaps(start, end):
                continue
            matched = text[start:end]
            para_idx = _paragraph_index_for_offset(text, start)
            suggestion = get_replacement(entry, tone, para_idx)
            hits.append(
                PatternHit(
                    category=entry.category,
                    matched=matched,
                    start=start,
                    end=end,
                    suggestion=suggestion,
                    entry_id=entry.id,
                )
            )
            occupied.append((start, end))
    return sorted(hits, key=lambda h: h.start)


def analyze_text(text: str, tone: str = "professional") -> AnalysisResult:
    if not text or not str(text).strip():
        return AnalysisResult()

    text = str(text)
    hits = find_phrase_hits(text, tone=tone)
    words = _word_count(text)
    transition_words = get_transition_words()

    transition_count = sum(
        1 for w in re.findall(r"\b\w+\b", text.lower()) if w in transition_words
    )
    em_dashes = _em_dash_count(text)
    contraction_ratio = _contraction_ratio(text)
    sent_std = _sentence_length_std(text)
    opener_uniformity = _uniform_opener_score(text)

    phrase_hits = len(hits)
    phrase_density = (phrase_hits / words) * 100
    transition_density = (transition_count / words) * 100

    phrase_component = min(1.0, phrase_density / 8.0)
    transition_component = min(1.0, transition_density / 5.0)
    em_component = min(1.0, em_dashes / max(words / 200, 0.5))
    low_contraction = max(0.0, 1.0 - contraction_ratio * 2) if words > 40 else 0.0
    low_variance = max(0.0, 1.0 - min(sent_std / 8.0, 1.0)) if words > 30 else 0.0
    opener_component = opener_uniformity

    breakdown = {
        "phrase_density": round(phrase_component * WEIGHT_PHRASE_DENSITY, 2),
        "transition_density": round(transition_component * WEIGHT_TRANSITION_DENSITY, 2),
        "em_dash": round(em_component * WEIGHT_EM_DASH, 2),
        "low_contraction": round(low_contraction * WEIGHT_LOW_CONTRACTION, 2),
        "low_sentence_variance": round(low_variance * WEIGHT_LOW_SENTENCE_VARIANCE, 2),
        "uniform_openers": round(opener_component * WEIGHT_UNIFORM_OPENERS, 2),
    }
    ai_score = min(100.0, sum(breakdown.values()))

    symptoms = {
        "phrase_hits": float(phrase_hits),
        "transition_count": float(transition_count),
        "em_dash_count": float(em_dashes),
        "contraction_ratio": round(contraction_ratio, 3),
        "sentence_length_std": round(sent_std, 2),
        "opener_uniformity": round(opener_uniformity, 3),
        "phrase_density_per_100_words": round(phrase_density, 2),
    }

    return AnalysisResult(
        hits=hits,
        symptoms=symptoms,
        ai_score=round(ai_score, 1),
        score_breakdown=breakdown,
        word_count=words,
    )


def analyze_paragraph(text: str, tone: str = "professional") -> AnalysisResult:
    """Score a single paragraph (for replacement cap weighting in v2)."""
    return analyze_text(text, tone=tone)


def format_analysis_for_prompt(analysis: AnalysisResult, max_items: int = 12) -> str:
    if not analysis.hits and analysis.ai_score < 20:
        return "No major AI phrase patterns detected; still vary rhythm and tone."

    lines = [f"Heuristic AI-likelihood before rewrite: {analysis.ai_score:.0f}/100."]
    if analysis.symptoms.get("em_dash_count", 0) > 1:
        lines.append(f"- Reduce em dashes (found {int(analysis.symptoms['em_dash_count'])}).")
    if analysis.symptoms.get("contraction_ratio", 1) < 0.25:
        lines.append("- Add natural contractions where appropriate.")
    if analysis.symptoms.get("sentence_length_std", 0) < 4:
        lines.append("- Vary sentence length more (mix short and long).")

    seen = set()
    for hit in analysis.hits[:max_items]:
        key = hit.matched.lower()
        if key in seen:
            continue
        seen.add(key)
        alt = f" → prefer '{hit.suggestion}'" if hit.suggestion else ""
        lines.append(f'- Avoid or rephrase [{hit.category}]: "{hit.matched}"{alt}')

    if len(analysis.hits) > max_items:
        lines.append(f"- ...and {len(analysis.hits) - max_items} more flagged phrases.")
    return "\n".join(lines)
