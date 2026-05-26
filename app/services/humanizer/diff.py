"""
Word-level diff for highlighting changes (original vs humanized).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List

from app.schemas.ai_humanizer import DiffSegment


def _tokenize(text: str) -> List[str]:
    """Split into words and whitespace tokens, preserving spacing."""
    if not text:
        return []
    return re.findall(r"\S+|\s+", text)


def _join_tokens(tokens: List[str]) -> str:
    return "".join(tokens)


def build_diff(original: str, humanized: str) -> List[DiffSegment]:
    """
    Produce segments for UI: equal / insert / delete relative to flowing humanized view.
    Uses humanized as the primary line; deletes show removed words from original.
    """
    orig_tokens = _tokenize(original)
    new_tokens = _tokenize(humanized)
    matcher = SequenceMatcher(None, orig_tokens, new_tokens)
    segments: List[DiffSegment] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            text = _join_tokens(new_tokens[j1:j2])
            if text:
                segments.append(DiffSegment(type="equal", text=text))
        elif tag == "insert":
            text = _join_tokens(new_tokens[j1:j2])
            if text:
                segments.append(DiffSegment(type="insert", text=text))
        elif tag == "delete":
            text = _join_tokens(orig_tokens[i1:i2])
            if text:
                segments.append(DiffSegment(type="delete", text=text))
        elif tag == "replace":
            del_text = _join_tokens(orig_tokens[i1:i2])
            ins_text = _join_tokens(new_tokens[j1:j2])
            if del_text:
                segments.append(DiffSegment(type="delete", text=del_text))
            if ins_text:
                segments.append(DiffSegment(type="insert", text=ins_text))

    return segments
