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
