"""Map audit findings and gaps to fix proposals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Merge order for combined preview (content fixes)
CONTENT_FIX_ORDER = (
    "append_sources_footer",
    "add_missing_sections",
    "add_faq_section",
    "fix_image_alt",
    "expand_content",
)

META_FIX_TYPES = frozenset({"add_meta_description"})


@dataclass
class FixPlanItem:
    fix_type: str
    target_field: str
    title: str
    description: str
    severity: str
    finding_refs: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


def _finding_refs(findings: list[dict[str, Any]], *needles: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        msg = str(f.get("message", "")).lower()
        cat = str(f.get("category", "")).lower()
        hay = f"{cat} {msg}"
        if any(n.lower() in hay for n in needles):
            refs.append(
                {
                    "category": f.get("category", ""),
                    "message": f.get("message", ""),
                    "severity": f.get("severity", "medium"),
                }
            )
    return refs


def plan_fixes(
    *,
    findings: list[dict[str, Any]] | None,
    gap_matrix: list[dict[str, Any]] | None,
    adsense_risks: list[dict[str, Any]] | None,
    audit_payload: dict[str, Any] | None,
    word_target_met: bool,
    word_count: int,
    word_target: int,
    article_type: str,
    has_meta: bool,
    meta_length: int,
    missing_alt_count: int,
    has_sources_footer: bool,
) -> list[FixPlanItem]:
    """Return ordered fix plan items (may be empty)."""
    findings = findings or []
    gap_matrix = gap_matrix or []
    adsense_risks = adsense_risks or []
    plans: list[FixPlanItem] = []

    # Meta description
    meta_refs = _finding_refs(findings, "meta description", "meta")
    if not has_meta or meta_length < 70 or meta_refs:
        plans.append(
            FixPlanItem(
                fix_type="add_meta_description",
                target_field="meta",
                title="Add or improve meta description",
                description="Generate an SEO meta description aligned with the post title and content.",
                severity="high" if not has_meta else "medium",
                finding_refs=meta_refs or [{"category": "seo", "message": "Meta description needs improvement"}],
            )
        )

    # Sources footer
    eeat_refs = _finding_refs(findings, "source", "last updated", "eeat")
    unsupported = [r for r in (adsense_risks or []) if r.get("code") == "unsupported_claims"]
    if not has_sources_footer or eeat_refs or unsupported:
        sev = "critical" if unsupported else "high" if eeat_refs else "medium"
        plans.append(
            FixPlanItem(
                fix_type="append_sources_footer",
                target_field="content",
                title="Append Last Updated / Sources footer",
                description="Add a standard Wikipedia-attributed sources footer for E-E-A-T.",
                severity=sev,
                finding_refs=eeat_refs
                or [{"category": "eeat", "message": "Missing Last Updated / Sources footer"}],
            )
        )

    # Missing sections from Tavily gaps
    missing_topics = [
        g.get("topic", "")
        for g in gap_matrix
        if isinstance(g, dict) and g.get("status") == "missing" and g.get("topic")
    ][:3]
    if missing_topics:
        depth_refs = _finding_refs(findings, "word target", "topic", "content depth", "gap")
        plans.append(
            FixPlanItem(
                fix_type="add_missing_sections",
                target_field="content",
                title=f"Add missing sections ({len(missing_topics)} topics)",
                description="Insert H2 sections covering competitor topics not yet in the post.",
                severity="high" if len(missing_topics) >= 2 else "medium",
                finding_refs=depth_refs
                or [{"category": "content_depth", "message": "Competitor topic gaps detected"}],
                payload={"topics": missing_topics},
            )
        )

    # FAQ section
    intent_refs = _finding_refs(findings, "faq", "search intent")
    recommended = (audit_payload or {}).get("recommended_topics") or []
    if intent_refs or (article_type in ("biography", "net_worth") and recommended):
        plans.append(
            FixPlanItem(
                fix_type="add_faq_section",
                target_field="content",
                title="Add FAQ section",
                description="Add an FAQ block addressing common search queries for this topic.",
                severity="medium",
                finding_refs=intent_refs
                or [{"category": "search_intent", "message": "FAQ section recommended"}],
                payload={"topics": recommended[:5] if recommended else []},
            )
        )

    # Image alt text
    if missing_alt_count > 0:
        img_refs = _finding_refs(findings, "alt", "image")
        plans.append(
            FixPlanItem(
                fix_type="fix_image_alt",
                target_field="content",
                title=f"Fix missing image alt text ({missing_alt_count})",
                description="Add descriptive alt attributes to images missing accessibility text.",
                severity="medium" if missing_alt_count < 3 else "high",
                finding_refs=img_refs
                or [{"category": "seo", "message": "Images missing alt text"}],
                payload={"max_images": min(missing_alt_count, 5)},
            )
        )

    # Expand content
    thin_refs = _finding_refs(findings, "word target", "thin", "shallow", "below")
    if not word_target_met or word_count < word_target or thin_refs:
        plans.append(
            FixPlanItem(
                fix_type="expand_content",
                target_field="content",
                title="Expand content to meet word target",
                description=f"Expand the article toward the {word_target}-word target for {article_type} posts.",
                severity="critical" if word_count < word_target * 0.6 else "high",
                finding_refs=thin_refs
                or [{"category": "content_depth", "message": f"Below word target ({word_count}/{word_target})"}],
                payload={"word_target": word_target},
            )
        )

    return plans
