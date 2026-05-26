"""
Lightweight meaning-drift check: entity-like token retention after LLM rewrite.
"""

from __future__ import annotations

import logging
import re
from typing import Set

logger = logging.getLogger(__name__)

_ENTITY_PATTERN = re.compile(
    r"\b(?:"
    r"\d{1,4}(?:,\d{3})*(?:\.\d+)?%?|"  # numbers / percentages
    r"\d{4}|"  # years
    r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|"  # Multi-word proper nouns
    r"[A-Z]{2,}"  # acronyms
    r")\b"
)


def extract_entity_tokens(text: str) -> Set[str]:
    if not text:
        return set()
    tokens = set()
    for m in _ENTITY_PATTERN.finditer(text):
        t = m.group(0).strip()
        if len(t) > 1:
            tokens.add(t.lower())
    return tokens


def entity_retention_ratio(original: str, rewritten: str) -> float:
    """Fraction of original entity-like tokens still present in rewrite."""
    orig = extract_entity_tokens(original)
    if not orig:
        return 1.0
    new = extract_entity_tokens(rewritten)
    kept = len(orig & new)
    return kept / len(orig)


def check_meaning_drift(
    original: str,
    rewritten: str,
    tone: str = "professional",
    threshold: float = 0.75,
) -> bool:
    """
    Return True if retention is acceptable. Log warning on casual + technical drift.
    """
    ratio = entity_retention_ratio(original, rewritten)
    if ratio >= threshold:
        return True
    logger.warning(
        "Humanizer meaning drift: entity retention %.2f (tone=%s, threshold=%.2f)",
        ratio,
        tone,
        threshold,
    )
    return ratio >= (threshold - 0.15 if tone != "casual" else threshold - 0.1)
