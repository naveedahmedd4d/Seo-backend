from typing import Any, Dict, List, Optional

RESEARCH_CONTEXT_MAX_LENGTH = 3500
RESEARCH_SNIPPET_MAX_LENGTH = 500


def format_research_context(data: Dict[str, Any]) -> Optional[str]:
    """Format Tavily research response into a string for LLM prompts."""
    if not data or not isinstance(data, dict):
        return None
    parts: List[str] = []
    if data.get("answer"):
        summary = (data["answer"] or "").strip()
        if summary:
            parts.append(f"Summary: {summary[:800]}")
    results = data.get("results") or []
    if not isinstance(results, list):
        return "\n\n".join(parts) if parts else None
    total_len = sum(len(p) for p in parts) + (2 * (len(parts) - 1)) if parts else 0
    for r in results:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "Source").strip()
        content = (r.get("content") or "").strip()
        if not content:
            continue
        snippet = content[:RESEARCH_SNIPPET_MAX_LENGTH]
        if len(content) > RESEARCH_SNIPPET_MAX_LENGTH:
            snippet += "..."
        block = f"[{title}]: {snippet}"
        if total_len + len(block) + 2 > RESEARCH_CONTEXT_MAX_LENGTH:
            break
        parts.append(block)
        total_len += len(block) + 2
    return "\n\n".join(parts) if parts else None


def extract_topical_mapping_terms(
    topical_mapping: Optional[Dict[str, Any]],
    *,
    limit: int = 16,
) -> List[str]:
    """Pull autocomplete and related-query strings from topical mapping payload."""
    if not topical_mapping or not isinstance(topical_mapping, dict):
        return []

    terms: List[str] = []
    seen: set[str] = set()

    for item in topical_mapping.get("autocomplete_suggestions") or []:
        if not isinstance(item, dict):
            continue
        value = (item.get("value") or "").strip()
        key = value.lower()
        if value and key not in seen:
            terms.append(value)
            seen.add(key)

    for item in topical_mapping.get("related_queries") or []:
        if not isinstance(item, dict):
            continue
        value = (item.get("query") or "").strip()
        key = value.lower()
        if value and key not in seen:
            terms.append(value)
            seen.add(key)
        if len(terms) >= limit:
            return terms[:limit]

    return terms[:limit]


def format_topical_mapping_block(topical_mapping: Optional[Dict[str, Any]]) -> str:
    """Format topical mapping for LLM prompts."""
    if not topical_mapping or not isinstance(topical_mapping, dict):
        return ""

    auto_vals = [
        (item.get("value") or "").strip()
        for item in (topical_mapping.get("autocomplete_suggestions") or [])
        if isinstance(item, dict) and (item.get("value") or "").strip()
    ][:8]
    related_vals = [
        (item.get("query") or "").strip()
        for item in (topical_mapping.get("related_queries") or [])
        if isinstance(item, dict) and (item.get("query") or "").strip()
    ][:8]

    if not auto_vals and not related_vals:
        return ""

    lines = ["Topical mapping:"]
    if auto_vals:
        lines.append(f"Autocomplete: {', '.join(auto_vals)}")
    if related_vals:
        lines.append(f"Related queries: {', '.join(related_vals)}")
    return "\n".join(lines)


def merge_topical_mapping_terms(
    existing: List[str],
    topical_mapping: Optional[Dict[str, Any]],
    *,
    limit: int = 12,
) -> List[str]:
    """Append topical mapping terms to a list without duplicates."""
    merged = [str(item).strip() for item in existing if str(item).strip()]
    seen = {item.lower() for item in merged}
    for term in extract_topical_mapping_terms(topical_mapping):
        key = term.lower()
        if key not in seen:
            merged.append(term)
            seen.add(key)
        if len(merged) >= limit:
            break
    return merged[:limit]
