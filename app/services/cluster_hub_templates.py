"""Cluster pillar-hub outline — listicle for a main keyword plus nested members."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from app.services.celebrity_outline_templates import (
    OutlineSection,
    _append_section_briefs,
    _h2,
    _h3,
    _section_to_heading_nodes,
    outline_to_markdown,
)

CLUSTER_HUB_TEMPLATE_ID = "cluster_hub_v1"
CLUSTER_HUB_TARGET_WORD_COUNT = 1400
CLUSTER_HUB_INTENT = "cluster_hub"

HUB_FORMAT_RULES = """LENGTH (required — do not stop early):
- Total article body: approximately 1,400 words (minimum 1,200, maximum 1,600).
- Write EVERY H2 in the outline, including one heading per cluster member.
- If a dollar amount is unknown, describe the person's role and income channel — never invent a figure and never write "Not publicly disclosed", "N/A", or "Unknown".

FORMATTING (required):
- Short paragraphs of 2–4 sentences.
- After the opening prose for each H2, include one markdown bullet list with 3–4 points.
- At a Glance table columns: Name | Role | Estimated net worth. Omit a cell (leave the row without that column value) if the value is unknown — do not fill placeholders.
- BANNED cell/prose phrases: "Not publicly disclosed", "Not publicly itemized", "N/A", "Unknown", "Undisclosed".
- Do not add a Table of Contents.
- Do NOT write an FAQ section, FAQ headings, or Q/A pairs in the body. FAQs are generated separately.
- Do NOT embed external hyperlinks. Mention Wikipedia in plain text when attribution is needed.
- Internal member profile links (relative paths like /name-net-worth) may be added after generation; do not invent external URLs."""

HUB_QUALITY_RULES = """HUB FIRST (critical):
- The Introduction MUST answer the pillar query in the first two sentences (who is in this set, the year, and how they are ranked or compared).
- H1 MUST include the pillar topic and the current year when the topic is time-sensitive.
- This is a listicle / comparison hub, not a single-person biography or net-worth profile.
- One H2 (or H3 under The List) per cluster member. Cover only the names in the Cluster members list — do not add extra people.
- Comparison table must include every cluster member by name. Omit the net-worth cell when research has no credible figure.
- BANNED: religion, faith, spirituality, church, prayer.
- Include this disclaimer once: "Net worth estimates are based on publicly available information and may vary."
- Do NOT include FAQ headings or Q/A pairs in the body.
- Relative internal links to member articles are allowed later; do not invent http(s) URLs."""


def normalize_cluster_members(subs: Optional[Sequence[str]]) -> List[str]:
    """Deduplicate and title-case cluster member names."""
    seen: set[str] = set()
    out: List[str] = []
    for raw in subs or []:
        name = " ".join(str(raw).split()).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def member_heading_slug(name: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"member_{index}_{slug or 'entity'}"


def get_cluster_hub_format_rules() -> str:
    return HUB_FORMAT_RULES


def get_cluster_hub_quality_rules() -> str:
    return HUB_QUALITY_RULES


def get_cluster_hub_template(
    topic: str,
    members: Optional[Sequence[str]] = None,
) -> List[OutlineSection]:
    """Fixed hub outline with one child heading per cluster member."""
    names = normalize_cluster_members(members)
    year = date.today().year
    member_children = [
        _h3(
            member_heading_slug(name, i),
            name,
            "celebrity_focus",
            (80, 120),
            f"80–120 words on {name}: role, why they are in this set, and a research-backed "
            "net worth figure only if available. No invented numbers. No placeholder phrases.",
        )
        for i, name in enumerate(names)
    ]
    list_brief = (
        f"One H3 per cluster member ({len(names)} names). Keep each card 80–120 words."
        if names
        else "No members supplied — write a short note that the list will be completed when names are added."
    )
    return [
        _h2(
            "intro",
            "Introduction",
            "intro",
            (140, 180),
            f"FIRST two sentences: answer \"{topic}\" for {year} — who is in the set and how they compare. "
            "Then one sentence on why readers search this now. Include the disclaimer once.",
        ),
        _h2(
            "at_a_glance",
            "At a Glance",
            "celebrity_focus",
            (120, 160),
            "Markdown table: Name | Role | Estimated net worth. One row per cluster member. "
            "Omit the net-worth cell when unknown. Never write Not publicly disclosed or N/A.",
        ),
        _h2(
            "methodology",
            "How We Ranked and Estimated",
            "celebrity_focus",
            (160, 200),
            "Assets minus liabilities; public disclosures vs third-party estimates; why figures differ. "
            "Wikipedia for biography, not as a wealth audit. Do not invent headline figures.",
        ),
        _h2(
            "the_list",
            "The List",
            "celebrity_focus",
            (400, 700),
            list_brief,
            member_children,
        ),
        _h2(
            "patterns",
            "What They Have in Common",
            "industry_context",
            (140, 180),
            "Shared income patterns across the set (salary vs business vs sport/contracts). "
            "Stay specific to the named members.",
        ),
        _h2(
            "close",
            "Looking Ahead",
            "conclusion",
            (80, 120),
            "Forward-looking close: figures change with new disclosures. No new unsourced numbers. No FAQ.",
        ),
    ]


def build_cluster_hub_outline(
    topic: str,
    members: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    return [_section_to_heading_nodes(s, topic) for s in get_cluster_hub_template(topic, members)]


def get_cluster_hub_section_slugs(members: Optional[Sequence[str]] = None) -> List[str]:
    slugs: List[str] = []

    def walk(sections: List[OutlineSection]) -> None:
        for s in sections:
            slugs.append(s.slug)
            walk(s.children)

    walk(get_cluster_hub_template("topic", members))
    return slugs


def format_cluster_members_block(members: Optional[Sequence[str]] = None) -> str:
    names = normalize_cluster_members(members)
    if not names:
        return "Cluster members: (none provided)"
    return "Cluster members:\n" + "\n".join(f"- {n}" for n in names)


def outline_to_cluster_hub_prompt_block(
    topic: str,
    members: Optional[Sequence[str]] = None,
    include_briefs: bool = True,
) -> str:
    names = normalize_cluster_members(members)
    outline = build_cluster_hub_outline(topic, names)
    parts = [
        format_cluster_members_block(names),
        "",
        "ARTICLE STRUCTURE (follow these exact H2/H3 headings):",
        outline_to_markdown(outline),
        "",
        get_cluster_hub_quality_rules(),
        "",
        get_cluster_hub_format_rules(),
    ]
    if include_briefs:
        parts.append("\nPer-section guidance:")
        for section in get_cluster_hub_template(topic, names):
            _append_section_briefs(parts, section, topic)
    return "\n".join(parts)


def is_cluster_hub_request(content_intent: Optional[str]) -> bool:
    return (content_intent or "").strip() == CLUSTER_HUB_INTENT
