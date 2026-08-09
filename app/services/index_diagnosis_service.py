import json
import logging
import re
from typing import Any, Optional

from app.services.openrouter_service import generate_content

logger = logging.getLogger(__name__)

ROOT_CAUSES = (
    "noindex",
    "robots_blocked",
    "thin_content",
    "duplicate_canonical",
    "crawl_error",
    "not_discovered",
    "quality_issue",
    "unknown",
)


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _fallback_diagnosis(gsc_result: dict[str, Any], live_signals: dict[str, Any]) -> dict[str, Any]:
    if live_signals.get("has_noindex"):
        root_cause = "noindex"
        explanation = "The live page has a noindex directive in meta robots or googlebot tags."
    elif (gsc_result.get("robots_txt_state") or "").upper() == "DISALLOWED":
        root_cause = "robots_blocked"
        explanation = "Google reports this URL is blocked by robots.txt."
    elif (live_signals.get("word_count") or 0) < 300:
        root_cause = "thin_content"
        explanation = "The page has very little visible text content, which often leads to non-indexing."
    elif (gsc_result.get("coverage_state") or "").lower().startswith("discovered"):
        root_cause = "not_discovered"
        explanation = "Google discovered the URL but has not crawled or indexed it yet."
    elif (live_signals.get("status_code") or 0) >= 400:
        root_cause = "crawl_error"
        explanation = f"The page returned HTTP {live_signals.get('status_code')} when fetched."
    else:
        root_cause = "quality_issue"
        explanation = (
            gsc_result.get("coverage_state")
            or "Google crawled the page but chose not to index it."
        )

    return {
        "root_cause": root_cause,
        "confidence": 0.5,
        "explanation": str(explanation),
        "recommended_fixes": [
            {
                "type": root_cause,
                "description": "Review the issue manually and apply the appropriate SEO fix.",
                "auto_applicable": False,
            }
        ],
    }


def _normalize_diagnosis(raw: dict[str, Any]) -> dict[str, Any]:
    root_cause = (raw.get("root_cause") or "unknown").lower()
    if root_cause not in ROOT_CAUSES:
        root_cause = "unknown"

    confidence = raw.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.5

    fixes = raw.get("recommended_fixes") or []
    if not isinstance(fixes, list):
        fixes = []
    normalized_fixes = []
    for fix in fixes[:5]:
        if not isinstance(fix, dict):
            continue
        normalized_fixes.append({
            "type": str(fix.get("type") or "unknown"),
            "description": str(fix.get("description") or ""),
            "auto_applicable": bool(fix.get("auto_applicable", False)),
        })

    return {
        "root_cause": root_cause,
        "confidence": confidence,
        "explanation": str(raw.get("explanation") or "Unable to determine root cause."),
        "recommended_fixes": normalized_fixes or _fallback_diagnosis({}, {}).get("recommended_fixes", []),
    }


async def diagnose_non_indexed_url(
    gsc_result: dict[str, Any],
    live_signals: dict[str, Any],
    wp_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Use LLM to diagnose why a URL is not indexed."""
    payload = {
        "gsc": {
            "verdict": gsc_result.get("verdict"),
            "coverage_state": gsc_result.get("coverage_state"),
            "indexing_state": gsc_result.get("indexing_state"),
            "robots_txt_state": gsc_result.get("robots_txt_state"),
            "page_fetch_state": gsc_result.get("page_fetch_state"),
            "google_canonical": gsc_result.get("google_canonical"),
            "user_canonical": gsc_result.get("user_canonical"),
            "last_crawl_time": (
                gsc_result.get("last_crawl_time").isoformat()
                if gsc_result.get("last_crawl_time") else None
            ),
        },
        "live_page": live_signals,
        "wordpress": wp_meta or {},
    }

    prompt = f"""You are an SEO technical auditor. A WordPress URL is NOT indexed in Google Search.

Analyze the data below and return ONLY valid JSON with this schema:
{{
  "root_cause": one of {list(ROOT_CAUSES)},
  "confidence": 0.0 to 1.0,
  "explanation": "2-3 sentence plain-English summary for a site owner",
  "recommended_fixes": [
    {{"type": "short_snake_case", "description": "specific action", "auto_applicable": false}}
  ]
}}

Data:
{json.dumps(payload, indent=2, default=str)}

Rules:
- Prefer the most likely single root cause.
- recommended_fixes: 1-3 items, practical and specific.
- auto_applicable should be false for content rewrites; true only for simple technical fixes like removing noindex.
- Do not include markdown or commentary outside the JSON object."""

    try:
        raw_text = await generate_content(
            prompt,
            max_tokens=800,
            temperature=0.2,
            system="You return only valid JSON objects for SEO diagnostics.",
        )
        parsed = _extract_json(raw_text)
        if parsed:
            return _normalize_diagnosis(parsed)
    except Exception as exc:
        logger.warning("LLM index diagnosis failed: %s", exc)

    return _fallback_diagnosis(gsc_result, live_signals)
