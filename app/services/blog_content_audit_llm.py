"""LLM structured audit for blog content review."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.services.blog_content_audit_config import LLM_SCORE_ADJUSTMENT_MAX, SCORE_WEIGHTS
from app.services.blog_content_audit_parser import ParsedPost
from app.services.openrouter_service import generate_content

logger = logging.getLogger(__name__)

AUDIT_SYSTEM = """You are an expert SEO and AdSense content quality auditor.
Return ONLY valid JSON matching the requested schema. No markdown fences.
Adjust scores at most ±15 from deterministic anchors; justify changes in findings."""


def _clamp(score: float) -> float:
    return max(0.0, min(100.0, round(score, 1)))


def _parse_json(text: str) -> Optional[dict[str, Any]]:
    cleaned = text.strip()
    for prefix in ("```json", "```"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _merge_scores(
    deterministic: dict[str, float],
    llm_scores: dict[str, Any],
) -> dict[str, float]:
    merged: dict[str, float] = {}
    for key in SCORE_WEIGHTS:
        base = float(deterministic.get(key, 50))
        llm_val = llm_scores.get(key)
        if llm_val is None:
            merged[key] = base
            continue
        try:
            adjusted = float(llm_val)
        except (TypeError, ValueError):
            merged[key] = base
            continue
        delta = adjusted - base
        if abs(delta) > LLM_SCORE_ADJUSTMENT_MAX:
            adjusted = base + (LLM_SCORE_ADJUSTMENT_MAX if delta > 0 else -LLM_SCORE_ADJUSTMENT_MAX)
        merged[key] = _clamp(adjusted)
    return merged


def _overall(scores: dict[str, float]) -> float:
    return _clamp(sum(scores[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS))


async def run_llm_content_audit(
    parsed: ParsedPost,
    *,
    deterministic_scores: dict[str, float],
    deterministic_findings: list[dict[str, Any]],
    gap_matrix: list[dict[str, Any]],
    adsense_risks: list[dict[str, Any]],
    skip_llm: bool = False,
) -> dict[str, Any]:
    """
    Run LLM audit or return deterministic-only payload when skip_llm=True.
    """
    if skip_llm:
        scores = dict(deterministic_scores)
        return {
            "scores": scores,
            "overall_score": _overall(scores),
            "findings": deterministic_findings,
            "gap_summary": "",
            "recommended_topics": [],
            "word_target_exception_reason": None,
        }

    excerpt = parsed.plain_text[:6000]
    headings = ", ".join(h["text"] for h in parsed.headings[:12])
    gaps_json = json.dumps(gap_matrix[:8], ensure_ascii=False)
    findings_json = json.dumps(deterministic_findings[:10], ensure_ascii=False)
    risks_json = json.dumps(adsense_risks[:6], ensure_ascii=False)
    scores_json = json.dumps(deterministic_scores)

    prompt = f"""Audit this blog post for SEO, content quality, and AdSense readiness.

Title: {parsed.title}
URL: {parsed.url}
Article type: {parsed.article_type}
Word count: {parsed.word_count} (target: {parsed.word_target})
Headings: {headings}

Deterministic score anchors (adjust ±{LLM_SCORE_ADJUSTMENT_MAX} max with justification):
{scores_json}

Deterministic findings:
{findings_json}

Tavily gap matrix:
{gaps_json}

AdSense risks:
{risks_json}

Post excerpt:
{excerpt}

Return JSON:
{{
  "scores": {{
    "seo": number,
    "content_quality": number,
    "search_intent": number,
    "content_depth": number,
    "originality": number,
    "eeat": number,
    "adsense_quality": number
  }},
  "overall_score": number,
  "findings": [
    {{"severity": "critical|high|medium|low", "category": "string", "message": "string", "evidence": "string"}}
  ],
  "gap_summary": "string",
  "recommended_topics": ["string"],
  "word_target_exception_reason": null or "string if topic genuinely needs fewer words"
}}"""

    try:
        raw = await generate_content(
            prompt,
            system=AUDIT_SYSTEM,
            max_tokens=2500,
            temperature=0.3,
            timeout_seconds=90.0,
        )
        parsed_json = _parse_json(raw)
    except Exception as exc:
        logger.warning("LLM content audit failed: %s", exc)
        parsed_json = None

    if not parsed_json or "scores" not in parsed_json:
        scores = dict(deterministic_scores)
        return {
            "scores": scores,
            "overall_score": _overall(scores),
            "findings": deterministic_findings,
            "gap_summary": "",
            "recommended_topics": [],
            "word_target_exception_reason": None,
        }

    merged_scores = _merge_scores(deterministic_scores, parsed_json.get("scores") or {})
    llm_findings = parsed_json.get("findings") or []
    if not isinstance(llm_findings, list):
        llm_findings = []

    # Merge findings: deterministic + LLM (dedupe by message)
    seen_msgs = {f.get("message", "") for f in deterministic_findings}
    combined = list(deterministic_findings)
    for f in llm_findings:
        if isinstance(f, dict) and f.get("message") and f["message"] not in seen_msgs:
            combined.append(
                {
                    "severity": f.get("severity", "medium"),
                    "category": f.get("category", "content_quality"),
                    "message": f.get("message", ""),
                    "evidence": f.get("evidence"),
                }
            )
            seen_msgs.add(f["message"])

    exception = parsed_json.get("word_target_exception_reason")
    word_target_met = parsed.word_count >= parsed.word_target
    if exception and isinstance(exception, str) and exception.strip():
        combined.append(
            {
                "severity": "low",
                "category": "content_depth",
                "message": f"Word target exception: {exception.strip()}",
                "evidence": "LLM-assessed genuine short-topic exception",
            }
        )

    overall = _overall(merged_scores)

    recommended = parsed_json.get("recommended_topics") or []
    if not isinstance(recommended, list):
        recommended = []

    return {
        "scores": merged_scores,
        "overall_score": overall,
        "findings": combined,
        "gap_summary": str(parsed_json.get("gap_summary") or ""),
        "recommended_topics": [str(t) for t in recommended[:8]],
        "word_target_exception_reason": exception if exception else None,
        "word_target_met": word_target_met or bool(exception),
    }
