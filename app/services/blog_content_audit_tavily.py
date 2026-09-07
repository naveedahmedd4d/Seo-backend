"""Tavily-powered content gap matrix for blog content audit."""

from __future__ import annotations

import re
from typing import Any, Optional

from app.services.trivly_service import get_content_suggestions

GapItem = dict[str, Any]


def _topic_from_result(result: dict[str, Any]) -> str:
    title = (result.get("title") or "").strip()
    if title:
        return title[:120]
    url = result.get("url") or ""
    if url:
        slug = url.rstrip("/").split("/")[-1]
        return slug.replace("-", " ")[:120]
    content = (result.get("content") or "")[:80]
    return content or "Topic"


def _extract_topics_from_serp(results: list[dict[str, Any]], limit: int = 8) -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    for r in results:
        topic = _topic_from_result(r).lower()
        key = re.sub(r"\s+", " ", topic).strip()
        if key and key not in seen:
            seen.add(key)
            topics.append(_topic_from_result(r))
        if len(topics) >= limit:
            break
    return topics


def _topic_coverage(topic: str, post_text: str, headings_text: str) -> str:
    """Return covered | partial | missing."""
    combined = f"{headings_text} {post_text}".lower()
    words = [w for w in re.findall(r"\w+", topic.lower()) if len(w) > 3]
    if not words:
        return "missing"
    hits = sum(1 for w in words if w in combined)
    ratio = hits / len(words)
    if ratio >= 0.7:
        return "covered"
    if ratio >= 0.35:
        return "partial"
    return "missing"


async def build_gap_matrix(
    *,
    query: str,
    post_text: str,
    headings: list[dict[str, str]],
    api_key: str,
    max_topics: int = 8,
) -> list[GapItem]:
    """
    Compare post content against Tavily SERP topics.
    Returns list of {topic, status, evidence}.
    """
    if not api_key:
        return []

    try:
        results = await get_content_suggestions(query, num_suggestions=max_topics, api_key=api_key)
    except Exception:
        return []

    topics = _extract_topics_from_serp(results, limit=max_topics)
    if not topics:
        # Fallback: derive topics from competitor titles in raw results
        topics = [_topic_from_result(r) for r in results[:max_topics] if r.get("title")]

    headings_text = " ".join(h.get("text", "") for h in headings)
    post_excerpt = post_text[:12000]

    matrix: list[GapItem] = []
    for topic in topics:
        status = _topic_coverage(topic, post_excerpt, headings_text)
        matrix.append(
            {
                "topic": topic,
                "status": status,
                "evidence": f"Matched against post headings and body ({status})",
            }
        )
    return matrix


def count_missing_gaps(matrix: list[GapItem]) -> int:
    return sum(1 for g in matrix if g.get("status") == "missing")


def top_missing_topics(matrix: list[GapItem], limit: int = 5) -> list[str]:
    return [g["topic"] for g in matrix if g.get("status") == "missing"][:limit]
